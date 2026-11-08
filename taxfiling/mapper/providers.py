"""Who proposes the column mapping: no model, Claude, or a model running on this computer.

All three receive the same OutboundPayload and return the same JSON answer, which
proposal.py checks before anything uses it.

For the two model providers, the HTTP client gets a request hook (wire_guard) that
runs on every request after the SDK has serialized it and before a byte is sent:

* the host must be on the provider's allow list (api.anthropic.com over HTTPS for
  Claude; localhost, 127.0.0.1 or ::1 for a local model), and the path /v1/messages;
* the body must pass boundary.check_wire_body() against the workbook's denylist;
* only then is the body written to the audit log, and the request allowed out.

If the hook raises, the request is not sent. The anthropic package is imported only
here, and only when a model provider is used, so the rest of the program has no
network library loaded at all.
"""
from __future__ import annotations

import ipaddress
import json
import re
from typing import Callable
from urllib.parse import urlsplit

from .audit import AuditLog
from .boundary import BoundaryViolation, Denylist, OutboundPayload, check_wire_body
from .heuristic import propose_offline
from .profile import WorkbookProfile

CLAUDE_URL = "https://api.anthropic.com"
CLAUDE_HOSTS = {"api.anthropic.com"}
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_LOCAL_URL = "http://localhost:11434"     # Ollama, which serves the Anthropic Messages API
LOOPBACK_NAMES = {"localhost"}


class ProviderError(Exception):
    """The model could not give a usable answer (refused, cut off, not JSON)."""


def is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def wire_guard(provider: str, deny: Denylist, audit: AuditLog, allow_host: Callable[[str], bool],
               require_https: bool) -> Callable:
    """The request hook that enforces the boundary on the exact bytes being sent."""
    def hook(request) -> None:
        url = str(request.url)
        try:
            if not allow_host(request.url.host):
                raise BoundaryViolation(f"host {request.url.host!r} is not allowed for provider {provider}")
            if require_https and request.url.scheme != "https":
                raise BoundaryViolation(f"provider {provider} must use HTTPS")
            if request.method != "POST" or not request.url.path.endswith("/v1/messages"):
                raise BoundaryViolation(f"only POST /v1/messages is allowed, not {request.method} {request.url.path}")
            body = request.read()
            check_wire_body(body, deny)
        except BoundaryViolation as exc:
            audit.blocked(provider, url, str(exc))
            raise
        audit.sent(provider, url, body)
    return hook


def _unwrap(exc: BaseException) -> BaseException:
    """The SDK wraps errors raised in a request hook; find a BoundaryViolation inside."""
    seen = exc
    while seen is not None:
        if isinstance(seen, BoundaryViolation):
            return seen
        seen = seen.__cause__ or seen.__context__
    return exc


def _json_from_text(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ProviderError("the model's answer has no JSON object in it")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"the model's answer is not valid JSON ({exc.msg})") from None


class OfflineProvider:
    """No model: matches headers against known names (heuristic.py). Sends nothing."""
    name = "none"
    endpoint = "no network: rules in taxfiling/mapper/heuristic.py"

    def __init__(self, audit: AuditLog):
        self.audit = audit

    def propose(self, payload: OutboundPayload, profile: WorkbookProfile) -> dict:
        self.audit.offline(self.name, payload.sha256, "mapping proposed by local rules; nothing was sent")
        # The rules read the same outbound view of the profile a model would get.
        return propose_offline(json.loads(payload.user)["workbook"])


class _MessagesProvider:
    """Shared code for providers that speak the Anthropic Messages API."""
    name = ""
    structured_output = True

    def __init__(self, model: str, deny: Denylist, audit: AuditLog, base_url: str,
                 allow_host: Callable[[str], bool], require_https: bool, api_key: str | None = None,
                 transport=None, effort: str | None = None, fallbacks: bool = False):
        try:
            import anthropic
        except ImportError:
            raise ProviderError("the anthropic package is not installed; run: python -m pip install -e \".[ai]\"") \
                from None
        self.model = model
        self.audit = audit
        self.endpoint = f"{base_url.rstrip('/')}/v1/messages"
        self.effort = effort
        self.fallbacks = fallbacks
        self._anthropic = anthropic
        hook = wire_guard(self.name, deny, audit, allow_host, require_https)
        # Redirects are not followed, so a 3xx answer cannot send the body on to another host
        # (the hook would block a request to another host anyway).
        http_client = anthropic.DefaultHttpxClient(
            event_hooks={"request": [hook]}, follow_redirects=False,
            **({"transport": transport} if transport else {}))
        kwargs = {"api_key": api_key} if api_key else {}
        self.client = anthropic.Anthropic(base_url=base_url, http_client=http_client,
                                         max_retries=0, **kwargs)   # one request per run, one log entry

    def _request(self, payload: OutboundPayload) -> dict:
        system = payload.system
        params: dict = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": payload.user}],
        }
        output_config = {}
        if self.effort:
            output_config["effort"] = self.effort
        if self.structured_output:
            output_config["format"] = {"type": "json_schema", "schema": payload.response_schema}
        else:
            params["system"] = (system + "\nAnswer with only one JSON object that matches this JSON schema:\n"
                                + json.dumps(payload.response_schema))
        if output_config:
            params["output_config"] = output_config
        return params

    def propose(self, payload: OutboundPayload, profile: WorkbookProfile) -> dict:
        if not isinstance(payload, OutboundPayload):
            raise TypeError("a provider only accepts an OutboundPayload from build_payload()")
        params = self._request(payload)
        anthropic = self._anthropic
        try:
            if self.fallbacks:
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
            else:
                response = self.client.messages.create(**params)
        except anthropic.APIConnectionError as exc:
            inner = _unwrap(exc)
            if isinstance(inner, BoundaryViolation):
                raise inner from None
            raise ProviderError(f"could not reach {self.endpoint}: {exc}") from None
        except anthropic.AuthenticationError:
            raise ProviderError("the API key was refused (set ANTHROPIC_API_KEY or run `ant auth login`)") from None
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"{self.endpoint} answered HTTP {exc.status_code}: {exc.message}") from None
        except TypeError as exc:
            # The SDK raises this before building the request when it finds no credentials.
            if "authentication" not in str(exc).lower():
                raise
            raise ProviderError("no Claude credentials found: set ANTHROPIC_API_KEY or run `ant auth login`. "
                                "Nothing was sent.") from None
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise ProviderError("the model's answer was cut off (max_tokens)")
        text = "".join(b.text for b in response.content if b.type == "text")
        self.audit.received(self.name, text)
        return _json_from_text(text)


class ClaudeProvider(_MessagesProvider):
    """Claude through Anthropic's API. Only api.anthropic.com over HTTPS is allowed."""
    name = "claude"

    def __init__(self, deny: Denylist, audit: AuditLog, model: str = DEFAULT_CLAUDE_MODEL,
                 effort: str = "medium", api_key: str | None = None, transport=None):
        # base_url is fixed, so ANTHROPIC_BASE_URL in the environment cannot redirect the
        # request; the host check would block it anyway.
        super().__init__(model, deny, audit, CLAUDE_URL, lambda host: host in CLAUDE_HOSTS, True,
                         api_key=api_key, transport=transport, effort=effort,
                         fallbacks=model == DEFAULT_CLAUDE_MODEL)


class LocalModelProvider(_MessagesProvider):
    """A model on this computer that serves the Anthropic Messages API (for example Ollama).
    Refuses any address that is not loopback, so 'local' cannot quietly mean remote."""
    name = "local"
    structured_output = False

    def __init__(self, deny: Denylist, audit: AuditLog, model: str, base_url: str = DEFAULT_LOCAL_URL,
                 transport=None):
        host = urlsplit(base_url).hostname or ""
        if not is_loopback(host):
            raise BoundaryViolation(f"--local-url must point at this computer (localhost, 127.0.0.1 or ::1), "
                                    f"not {host!r}")
        super().__init__(model, deny, audit, base_url, is_loopback, False, api_key="local-model",
                         transport=transport)
