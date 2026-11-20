"""The demo screen: same output as the command line, SSNs never sent to the browser, and a
server that only answers this computer and its own page."""
import http.client
import json
import re
import threading

import pytest

from taxfiling.demo.app import Demo, DemoError
from taxfiling.demo.server import serve

from .conftest import VA_GOLDEN, example

def all_ssns():
    from taxfiling.readers.excel import load_mapping, read_w2_workbook
    _, employees, _ = read_w2_workbook(example(2025)[0], load_mapping("w2_standard"))
    return {e.ssn for e in employees}


def assert_no_ssn(obj):
    text = json.dumps(obj, default=str)
    for ssn in all_ssns():
        assert ssn not in text
        assert f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}" not in text


@pytest.fixture
def demo():
    d = Demo()
    yield d
    d.close()


def choice_from(proposal, **extra):
    return {"sheet": proposal["sheet"], "box12": proposal["box12"],
            "columns": {k: v["column"] for k, v in proposal["columns"].items() if v["column"]}, **extra}


def test_tidy_sample_makes_the_golden_file_offline(demo, no_network):
    info = demo.open_sample("va_2025")
    proposal = demo.propose(info["session"], "none")
    assert proposal["missing_required"] == []
    assert [e["event"] for e in proposal["audit"]] == ["no_request"]
    result = demo.build(info["session"], choice_from(proposal, output="w2", year=2025))
    assert result["check"]["ok"] and result["download"]
    name, data = demo.download(info["session"], result["download"])
    assert name == "W2 REPORT 2025.txt"
    assert data == VA_GOLDEN.read_bytes()
    for response in (info, proposal, result):
        assert_no_ssn(response)


def test_messy_sample_needs_two_choices_then_matches_the_golden_file(demo, no_network):
    info = demo.open_sample("va_2025_messy")
    proposal = demo.propose(info["session"], "none")
    assert sorted(proposal["missing_required"]) == ["box15_state", "state"]
    assert not any(re.search(r"S\d+\.C\d+", n) for n in proposal["notes"])   # letters, not ids
    choice = choice_from(proposal, output="w2", year=2025)
    with pytest.raises(DemoError, match="required"):
        demo.build(info["session"], choice)
    cols = {c["header"]: c["id"] for c in info["sheets"][0]["columns"]}
    choice["columns"].update(state=cols["St"], box15_state=cols["Work State"])
    result = demo.build(info["session"], choice)
    assert demo.download(info["session"], result["download"])[1] == VA_GOLDEN.read_bytes()
    with pytest.raises(DemoError, match="Quarterly Wages"):
        demo.build(info["session"], {**choice, "output": "ui", "quarter": 1})


def test_a_wrong_choice_is_checked_like_a_model_answer(demo):
    info = demo.open_sample("va_2025")
    proposal = demo.propose(info["session"], "none")
    choice = choice_from(proposal, output="w2", year=2025)
    choice["columns"]["last_name"] = choice["columns"]["first_name"]
    with pytest.raises(DemoError, match="used for both"):
        demo.build(info["session"], choice)


@pytest.mark.parametrize("quarter", [1, 4])
def test_quarterly_file_matches_the_committed_example(demo, quarter):
    info = demo.open_sample("va_2025")
    proposal = demo.propose(info["session"], "none")
    result = demo.build(info["session"], choice_from(proposal, output="ui", year=2025, quarter=quarter))
    assert result["provisional"] and result["check"]["ok"]
    golden = VA_GOLDEN.parent / f"VEC UI 2025 Q{quarter}.txt"
    assert demo.download(info["session"], result["download"])[1] == golden.read_bytes()
    assert_no_ssn(result)


def test_preview_masks_every_ssn(demo):
    info = demo.open_sample("va_2025")
    proposal = demo.propose(info["session"], "none")
    result = demo.build(info["session"], choice_from(proposal, output="w2", year=2025))
    assert {p["record"] for p in result["preview"]} >= {"RA", "RE", "RW", "RS", "RT", "RF"}
    assert all(p["text"][2:7] == "*****" for p in result["preview"] if p["record"] == "RW")
    assert_no_ssn(result["preview"])


def test_claude_mode_is_refused_without_a_key(demo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    info = demo.open_sample("va_2025")
    assert info["claude"]["available"] is False
    with pytest.raises(DemoError):
        demo.propose(info["session"], "claude")


def test_uploads_must_be_workbooks_and_the_temp_folder_is_removed():
    d = Demo()
    with pytest.raises(DemoError, match="Excel"):
        d.open_upload(b"SSN,Name\n123-45-6789,A\n", "w2.csv")
    info = d.open_upload(example(2025)[0].read_bytes(), "../../evil name.xlsx")
    assert info["name"] == "evil name.xlsx"
    folder = d.folder
    assert folder.exists()
    d.close()
    assert not folder.exists()


@pytest.fixture
def server():
    ready, ref = threading.Event(), []
    t = threading.Thread(target=serve, kwargs={"port": 0, "open_browser": False, "ready": ready, "server_ref": ref},
                         daemon=True)
    t.start()
    ready.wait(5)
    httpd, key, port = ref
    yield key, port
    httpd.shutdown()


def call(port, method, path, host=None, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    data = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    res = conn.getresponse()
    return res.status, dict(res.getheaders()), res.read()


def test_server_guards(server):
    key, port = server
    status, headers, page = call(port, "GET", "/")
    assert status == 200 and key.encode() in page
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert call(port, "GET", "/", host="attacker.example")[0] == 403          # DNS rebinding
    assert call(port, "GET", "/api/options")[0] == 403                        # no key
    assert call(port, "POST", "/api/sample", headers={"X-Demo-Key": "wrong"}, body={"sample": "va_2025"})[0] == 403
    assert call(port, "GET", "/static/../server.py")[0] == 404
    assert call(port, "GET", "/static/app.js")[0] == 200


def test_server_end_to_end(server):
    key, port = server
    h = {"X-Demo-Key": key}
    status, _, body = call(port, "POST", "/api/sample", headers=h, body={"sample": "va_2025"})
    info = json.loads(body)
    _, _, body = call(port, "POST", "/api/propose", headers=h, body={"session": info["session"], "ai": "none"})
    proposal = json.loads(body)
    _, _, body = call(port, "POST", "/api/build", headers=h,
                      body={"session": info["session"], **choice_from(proposal, output="w2", year=2025)})
    result = json.loads(body)
    status, headers, data = call(port, "GET", f"/api/download?session={info['session']}&file={result['download']}&key={key}")
    assert status == 200 and "W2 REPORT 2025.txt" in headers["Content-Disposition"]
    assert data == VA_GOLDEN.read_bytes()
    status, _, body = call(port, "POST", "/api/propose", headers=h, body={"session": "nope", "ai": "none"})
    assert status == 400 and "out of date" in json.loads(body)["error"]
