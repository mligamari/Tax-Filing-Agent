"""The tool must work with networking switched off, and must not print SSNs."""
import socket

import pytest

from taxfiling.cli import main

from .conftest import VA_SUBMITTER, VA_WORKBOOK


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_full_run_needs_no_network_and_prints_no_ssn(no_network, tmp_path, capsys, va_report):
    out = tmp_path / "W2 REPORT 2026.txt"
    code = main(["w2", "--state", "VA", "--input", str(VA_WORKBOOK), "--submitter", str(VA_SUBMITTER),
                 "--out", str(out)])
    assert code == 0
    assert main(["check", str(out), "--state", "VA", "--input", str(VA_WORKBOOK)]) == 0
    assert main(["show", str(out), "--limit", "10"]) == 0
    printed = capsys.readouterr().out
    assert "Round-trip check: PASSED" in printed
    file_text = out.read_text()
    for w2 in va_report.employees:
        assert w2.ssn in file_text           # the file itself carries full SSNs, as required
        assert w2.ssn not in printed         # the console never does
        assert f"{w2.ssn[:3]}-{w2.ssn[3:5]}-{w2.ssn[5:]}" not in printed
