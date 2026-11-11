"""The model providers, against a mock HTTP transport: no network, no API key needed.

The mock sits where the network would be, below the SDK and below the request hook,
so these tests see the bytes exactly as they would have gone out.
"""
import json

import pytest

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")

from taxfiling.mapper import AuditLog, BoundaryViolation, prepare  # noqa: E402
from taxfiling.mapper.audit import CREDENTIAL_PLACEHOLDER  # noqa: E402
from taxfiling.mapper.boundary import CREDENTIAL_HEADERS  # noqa: E402
from taxfiling.mapper.providers import (  # noqa: E402
    ClaudeProvider, LocalModelProvider, OfflineProvider, ProviderError, is_loopback,
)

from .conftest import MESSY_WORKBOOK, make_report  # noqa: E402
from .test_mapper_boundary import assert_no_sensitive_values  # noqa: E402

FAKE_KEY = "sk-ant-test-0000-do-not-log-me"


class FakeAPI:
    """Answers POST /v1/messages like the Messages API, and records what reached it."""

    def __init__(self, answer: dict | str, stop_reason: str = "end_turn"):
        self.answer = answer if isinstance(answer, str) else json.dumps(answer)
        self.stop_reason = stop_reason
        self.requests: list = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": self.answer}], "stop_reason": self.stop_reason,
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})

    def transport(self):
        return httpx.MockTransport(self)


@pytest.fixture(scope="module")
def messy():
    return prepare(MESSY_WORKBOOK)


def expected_answer(prepared) -> dict:
    from examples.va_2025_messy.make_messy_workbook import EXPECTED
    col = {c.header: c.id for c in prepared.profile.sheets[0].columns}
    return {"sheet": "S1", "notes": [], "box12": [
        {"code_column": col[f"12{s} Cd"], "amount_column": col[f"12{s} Amt"]} for s in "ABCD"],
        "columns": [{"field": f, "column": col[h], "confidence": "high", "reason": "header"} for f, h in EXPECTED.items()]}


def test_claude_request_is_checked_logged_and_clean(messy, tmp_path, no_network):
    api = FakeAPI(expected_answer(messy))
    log = tmp_path / "audit.jsonl"
    provider = ClaudeProvider(messy.deny, AuditLog(log), api_key=FAKE_KEY, transport=api.transport())
    answer = provider.propose(messy.payload, messy.profile)
    assert answer == expected_answer(messy)

    (request,) = api.requests
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    wire = request.content
    body = json.loads(wire)
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == messy.payload.system
    assert body["messages"] == [{"role": "user", "content": messy.payload.user}]
    assert body["output_config"]["format"]["schema"] == messy.payload.response_schema
    assert body["max_tokens"] == 16000      # the one number allowed 5 digits
    assert_no_sensitive_values(wire.decode().replace('"max_tokens":16000', ""), make_report(2025))

    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e["event"] for e in entries] == ["request_sent", "response_received"]
    # The log holds the request as it reached the network: the URL, every header, the exact body.
    assert entries[0]["url"] == str(request.url)
    assert entries[0]["body"].encode() == wire
    sent_headers = dict(request.headers.items())
    assert any(name in CREDENTIAL_HEADERS for name in sent_headers)
    assert entries[0]["headers"] == {name: CREDENTIAL_PLACEHOLDER if name in CREDENTIAL_HEADERS else value
                                     for name, value in sent_headers.items()}
    assert FAKE_KEY not in log.read_text()                 # the one thing left out is the API key


def test_the_wire_guard_stops_a_request_that_skips_build_payload(messy, tmp_path):
    """Call the SDK client directly with workbook data: the hook blocks it before the transport."""
    api = FakeAPI("{}")
    log = tmp_path / "audit.jsonl"
    provider = ClaudeProvider(messy.deny, AuditLog(log), api_key=FAKE_KEY, transport=api.transport())
    w = make_report(2025).employees[0]
    leak = f"{w.first_name} {w.last_name} {w.ssn[:3]}-{w.ssn[3:5]}-{w.ssn[5:]}"
    with pytest.raises(anthropic.APIConnectionError) as info:
        provider.client.messages.create(model="claude-opus-5-5", max_tokens=10,
                                        messages=[{"role": "user", "content": leak}])
    assert api.requests == []                              # nothing reached the network
    assert isinstance(info.value.__cause__, BoundaryViolation)
    text = log.read_text()
    assert "request_blocked" in text and w.ssn[-4:] not in text and w.last_name not in text


@pytest.mark.parametrize("where", ["query string", "extra header", "header name", "header value",
                                   "path", "user name in the URL"])
def test_the_wire_guard_checks_the_url_and_headers_not_only_the_body(messy, tmp_path, where):
    """Workbook data put anywhere in a request, not just in its body, is stopped before the
    transport, and neither the log nor the error repeats it."""
    api = FakeAPI("{}")
    log = tmp_path / "audit.jsonl"
    provider = ClaudeProvider(messy.deny, AuditLog(log), api_key=FAKE_KEY, transport=api.transport())
    w = make_report(2025).employees[0]
    dashed = f"{w.ssn[:3]}-{w.ssn[3:5]}-{w.ssn[5:]}"
    client, extra = provider.client, {}
    if where == "query string":
        extra = {"extra_query": {"employee": dashed}}
    elif where == "extra header":
        extra = {"extra_headers": {"x-employee": w.last_name}}
    elif where == "header name":
        extra = {"extra_headers": {f"x-stainless-{w.ssn}": "1"}}
    elif where == "header value":
        extra = {"extra_headers": {"anthropic-beta": w.last_name}}
    elif where == "path":
        client = client.with_options(base_url=f"https://api.anthropic.com/{w.ssn}")
    else:
        client = client.with_options(base_url=f"https://{w.last_name}@api.anthropic.com")
    with pytest.raises(anthropic.APIConnectionError) as info:
        client.messages.create(model="claude-opus-5-5", max_tokens=10,
                               messages=[{"role": "user", "content": "hi"}], **extra)
    assert api.requests == []                              # nothing reached the network
    assert isinstance(info.value.__cause__, BoundaryViolation)
    text, error = log.read_text(), str(info.value.__cause__)
    assert "request_blocked" in text
    for value in (w.ssn, dashed, w.ssn[-4:], w.last_name):
        assert value not in text and value not in error


def test_a_login_profiles_headers_pass_the_guard(messy):
    """`ant auth login` adds a workspace ID header and an OAuth flag to anthropic-beta.
    Neither comes from the workbook, so neither stops the request."""
    api = FakeAPI(expected_answer(messy))
    provider = ClaudeProvider(messy.deny, AuditLog(None), api_key=FAKE_KEY, transport=api.transport())
    client = provider.client.with_options(default_headers={"anthropic-workspace-id": "wrkspc_01Ab23456cDe"})
    client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}],
                           extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01,oauth-2025-04-20"})
    assert len(api.requests) == 1 and api.requests[0].headers["anthropic-workspace-id"] == "wrkspc_01Ab23456cDe"


def test_missing_credentials_send_nothing(messy, monkeypatch, tmp_path):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_IDENTITY_TOKEN",
                "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    api = FakeAPI("{}")
    provider = ClaudeProvider(messy.deny, AuditLog(tmp_path / "audit.jsonl"), transport=api.transport())
    with pytest.raises(ProviderError, match="no Claude credentials"):
        provider.propose(messy.payload, messy.profile)
    assert api.requests == [] and not (tmp_path / "audit.jsonl").exists()


def test_provider_accepts_only_a_checked_payload(messy):
    provider = ClaudeProvider(messy.deny, AuditLog(None), api_key=FAKE_KEY, transport=FakeAPI("{}").transport())
    with pytest.raises(TypeError, match="OutboundPayload"):
        provider.propose({"system": "x", "user": "y"}, messy.profile)


def test_claude_host_cannot_be_redirected(messy, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://collector.example.net")
    api = FakeAPI(expected_answer(messy))
    provider = ClaudeProvider(messy.deny, AuditLog(None), api_key=FAKE_KEY, transport=api.transport())
    provider.propose(messy.payload, messy.profile)
    assert api.requests[0].url.host == "api.anthropic.com"
    # and a client pointed elsewhere by code is stopped by the host allow list
    moved = provider.client.with_options(base_url="https://collector.example.net")
    with pytest.raises(anthropic.APIConnectionError) as info:
        moved.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert "not allowed" in str(info.value.__cause__)
    assert len(api.requests) == 1


def test_a_redirect_cannot_send_the_body_elsewhere(messy):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(307, headers={"location": "https://collector.example.net/v1/messages"})
    provider = ClaudeProvider(messy.deny, AuditLog(None), api_key=FAKE_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="307"):
        provider.propose(messy.payload, messy.profile)
    assert seen == ["api.anthropic.com"]


def test_local_model_must_be_on_this_computer(messy):
    for url in ("http://192.168.1.20:11434", "http://my-gpu-box:11434", "https://api.example.com"):
        with pytest.raises(BoundaryViolation, match="this computer"):
            LocalModelProvider(messy.deny, AuditLog(None), model="qwen3", base_url=url)
    assert is_loopback("localhost") and is_loopback("127.0.0.1") and is_loopback("::1")
    assert not is_loopback("10.0.0.5")


def test_local_model_gets_the_same_payload_and_answer_format(messy, no_network):
    api = FakeAPI("Here you go:\n```json\n" + json.dumps(expected_answer(messy)) + "\n```")
    provider = LocalModelProvider(messy.deny, AuditLog(None), model="qwen3", base_url="http://127.0.0.1:11434",
                                  transport=api.transport())
    assert provider.propose(messy.payload, messy.profile) == expected_answer(messy)
    body = json.loads(api.requests[0].content)
    assert api.requests[0].url.host == "127.0.0.1"
    assert body["messages"][0]["content"] == messy.payload.user
    assert "output_config" not in body and "JSON schema" in body["system"]


@pytest.mark.parametrize("stop_reason,text,error", [
    ("refusal", "", "declined"), ("max_tokens", "{", "cut off"), ("end_turn", "no json here", "no JSON"),
])
def test_unusable_answers_raise(messy, stop_reason, text, error):
    api = FakeAPI(text, stop_reason)
    provider = ClaudeProvider(messy.deny, AuditLog(None), api_key=FAKE_KEY, transport=api.transport())
    with pytest.raises(ProviderError, match=error):
        provider.propose(messy.payload, messy.profile)


def test_offline_provider_sends_nothing(messy, tmp_path, no_network):
    log = tmp_path / "audit.jsonl"
    answer = OfflineProvider(AuditLog(log)).propose(messy.payload, messy.profile)
    assert answer["sheet"] == "S1"
    (entry,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert entry["event"] == "no_request" and entry["payload_sha256"] == messy.payload.sha256
