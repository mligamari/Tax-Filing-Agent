"""The tool must work with networking switched off, and must not print SSNs."""
from taxfiling.cli import main

from .conftest import VA_SUBMITTER, VA_WORKBOOK


def test_full_run_needs_no_network_and_prints_no_ssn(no_network, tmp_path, capsys, va_report):
    out = tmp_path / "W2 REPORT 2025.txt"
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


def test_no_network_library_is_loaded_unless_a_model_is_used(tmp_path):
    """w2, check, show and `map --ai none` never import the anthropic SDK or an HTTP client."""
    import subprocess
    import sys

    from .conftest import MESSY_WORKBOOK, ROOT
    script = f"""
import sys
from taxfiling.cli import main
out = {str(tmp_path / 'out.txt')!r}
assert main(["w2", "--state", "VA", "--input", {str(VA_WORKBOOK)!r}, "--submitter", {str(VA_SUBMITTER)!r},
             "--out", out]) == 0
assert main(["check", out, "--state", "VA"]) == 0
main(["map", "--input", {str(MESSY_WORKBOOK)!r}, "--log", {str(tmp_path / 'a.jsonl')!r}])
loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("anthropic", "httpx", "httpx2", "requests", "urllib3"))
assert not loaded, loaded
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
