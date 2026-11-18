"""From a messy spreadsheet to a checked state file, through the command line.

The messy workbook holds the same people and amounts as the standard 2025 example, so
a correct mapping must reproduce the committed Virginia file byte for byte. The model
is replaced by a mock transport inside the HTTP client (after the privacy check), and
sockets are disabled for the whole run.
"""
import functools
import json

import pytest

from taxfiling.cli import main

from .conftest import MESSY_WORKBOOK, VA_GOLDEN, VA_SUBMITTER, VA_WORKBOOK, make_report


def write_and_compare(mapping, workbook, tmp_path) -> None:
    out = tmp_path / "W2 REPORT 2025.txt"
    assert main(["w2", "--state", "VA", "--input", str(workbook), "--submitter", str(VA_SUBMITTER),
                 "--mapping", str(mapping), "--out", str(out)]) == 0
    assert out.read_bytes() == VA_GOLDEN.read_bytes()


def test_messy_workbook_mapped_by_claude_gives_the_same_file(tmp_path, monkeypatch, capsys, no_network):
    pytest.importorskip("anthropic")
    from taxfiling.mapper import prepare, providers

    from .test_mapper_providers import FAKE_KEY, FakeAPI, expected_answer
    api = FakeAPI(expected_answer(prepare(MESSY_WORKBOOK)))
    monkeypatch.setattr(providers, "ClaudeProvider",
                        functools.partial(providers.ClaudeProvider, transport=api.transport(), api_key=FAKE_KEY))
    mapping, log = tmp_path / "messy.mapping.json", tmp_path / "audit.jsonl"
    code = main(["map", "--input", str(MESSY_WORKBOOK), "--ai", "claude", "--yes",
                 "--out", str(mapping), "--log", str(log)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert len(api.requests) == 1
    saved = json.loads(mapping.read_text())
    assert saved["proposed_by"]["provider"] == "claude"
    assert saved["proposed_by"]["payload_sha256"][:16] in printed
    write_and_compare(mapping, MESSY_WORKBOOK, tmp_path)

    report = make_report(2025)
    everything_printed = printed + capsys.readouterr().out + log.read_text()
    for w in report.employees:
        assert w.ssn not in everything_printed and f"{w.ssn[:3]}-{w.ssn[3:5]}-{w.ssn[5:]}" not in everything_printed


def test_no_ai_mode_on_the_standard_workbook_needs_no_network(tmp_path, no_network):
    mapping = tmp_path / "standard.mapping.json"
    assert main(["map", "--input", str(VA_WORKBOOK), "--out", str(mapping), "--log", str(tmp_path / "a.jsonl")]) == 0
    standard = json.loads((VA_WORKBOOK.parents[2] / "taxfiling/mappings/w2_standard.json").read_text())
    proposed = json.loads(mapping.read_text())
    assert proposed["columns"] == {k: v for k, v in standard["columns"].items() if k in proposed["columns"]}
    assert proposed["box12"] == standard["box12"]
    write_and_compare(mapping, VA_WORKBOOK, tmp_path)


def test_no_ai_mode_on_the_messy_workbook_asks_a_person(tmp_path, capsys, no_network):
    """The fixed rules cannot tell 'St' from 'Work State'. They say so and write nothing."""
    mapping = tmp_path / "messy.mapping.json"
    assert main(["map", "--input", str(MESSY_WORKBOOK), "--out", str(mapping), "--log", str(tmp_path / "a.jsonl")]) == 1
    printed = capsys.readouterr().out
    assert "Required fields not mapped: state, box15_state" in printed
    assert "left unmapped for you to choose" in printed
    assert not mapping.exists()


def test_dry_run_prints_the_payload_and_sends_nothing(tmp_path, capsys, no_network):
    assert main(["map", "--input", str(MESSY_WORKBOOK), "--ai", "claude", "--dry-run",
                 "--log", str(tmp_path / "a.jsonl")]) == 0
    printed = capsys.readouterr().out
    assert "Dry run: nothing was sent" in printed and '"header": "Social Sec #"' in printed
    assert not (tmp_path / "a.jsonl").exists()


def test_sending_needs_consent(tmp_path, capsys, monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["map", "--input", str(MESSY_WORKBOOK), "--ai", "claude", "--log", str(tmp_path / "a.jsonl")]) == 1
    assert "add --yes" in capsys.readouterr().err


def test_committed_messy_mapping_gives_the_same_file(tmp_path, no_network):
    """The example mapping in the README (rules plus two lines added by hand)."""
    write_and_compare(MESSY_WORKBOOK.parent / "w2_2025_va_messy.mapping.json", MESSY_WORKBOOK, tmp_path)


def test_the_payload_excerpt_in_the_readme_is_what_is_actually_sent():
    """The README shows four lines of a real payload; each must appear verbatim in it."""
    from taxfiling.mapper import prepare
    readme = (MESSY_WORKBOOK.parents[2] / "README.md").read_text()
    block = readme.split("This is the actual text sent for four columns", 1)[1].split("```text", 1)[1]
    excerpt = [line.strip() for line in block.split("```", 1)[0].strip().splitlines()]
    sent = {line.strip() for line in prepare(MESSY_WORKBOOK).payload.user.splitlines()}
    assert len(excerpt) == 4
    for line in excerpt:
        assert line in sent, line

