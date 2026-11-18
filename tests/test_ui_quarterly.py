"""Quarterly unemployment-insurance (UI) file for the Virginia Employment Commission, in SSA's
generic EFW2 RS-record format (provisional until VEC's own specification is in hand)."""
from pathlib import Path

import pytest

from taxfiling.check_quarterly import check_quarterly_file
from taxfiling.cli import main
from taxfiling.config import apply_employer_settings, load_submitter
from taxfiling.formats.efw2_quarterly import build_efw2_quarterly, reporting_period
from taxfiling.layout import load_layout
from taxfiling.readers.quarterly import load_quarterly_report
from taxfiling.states import report_profile

from .conftest import ROOT, example


def ui_report(year: int, quarter: int):
    workbook, submitter_file, _ = example(year)
    report, issues = load_quarterly_report(workbook, "VA", year, quarter)
    assert not issues.errors
    submitter, settings = load_submitter(submitter_file)
    apply_employer_settings(report.employer, settings, submitter)
    report.submitter = submitter
    return report


def build(year: int, quarter: int, report=None):
    profile = report_profile("VA", "ui_quarterly", year)
    layout = load_layout(profile["layout"])
    report = report or ui_report(year, quarter)
    text, issues = build_efw2_quarterly(report, layout, profile)
    return text, issues, report, layout, profile


def golden(year: int, quarter: int) -> Path:
    return ROOT / "examples" / f"va_{year}" / "output" / f"VEC UI {year} Q{quarter}.txt"


@pytest.mark.parametrize("year,quarter", [(y, q) for y in (2025, 2026) for q in (1, 2, 3, 4)])
def test_matches_the_committed_golden_file(year, quarter):
    text, issues, *_ = build(year, quarter)
    assert not issues.errors
    assert text.encode("ascii") == golden(year, quarter).read_bytes()


def test_reporting_period_is_the_quarters_last_month():
    assert [reporting_period(2025, q) for q in (1, 2, 3, 4)] == ["032025", "062025", "092025", "122025"]


def test_file_shape_and_ui_fields_at_their_positions():
    text, issues, report, *_ = build(2025, 2)
    lines = text.split("\r\n")
    assert [line[:2] for line in lines] == ["RA", "RE"] + ["RS"] * len(report.employees) + ["RF"]
    assert all(len(line) == 512 for line in lines)
    assert not text.endswith("\r\n")
    rs = lines[2]
    first = report.employees[0]
    assert rs[2:4] == "51"                                   # Virginia state code
    assert rs[9:18] == first.ssn
    assert rs[196:202] == "062025"                           # reporting period
    assert rs[202:213] == f"{int(first.gross_wages * 100):011d}"
    assert rs[213:224] == f"{int(first.taxable_wages * 100):011d}"
    assert rs[247:267].rstrip() == "0918274"                 # VEC account number
    assert rs[275:297] == "0" * 22                           # no income tax data
    assert lines[-1][7:16] == "000000000"                    # RF: no RW records
    assert lines[1][2:6] == "2025"


def test_provisional_status_is_always_reported():
    _, issues, *_ = build(2025, 1)
    assert any("PROVISIONAL" in w.message for w in issues.warnings)


def test_round_trip_compares_every_employee():
    text, _, report, layout, profile = build(2025, 1)
    result = check_quarterly_file(text.encode("ascii"), layout, profile, 2025, 1, source=report)
    assert result.ok, result.issues.errors
    assert result.employees_compared == len(report.employees)
    assert result.totals["ui_total_wages"] == sum(e.gross_wages for e in report.employees)


def test_missing_ui_account_stops_the_file():
    report = ui_report(2025, 1)
    report.employer.ui_accounts.clear()
    text, issues, *_ = build(2025, 1, report)
    assert text is None
    assert any("unemployment-insurance account" in i.message for i in issues.errors)


# ---- the checker catches damage -------------------------------------------------

def _damaged(edit):
    text, _, report, layout, profile = build(2025, 1)
    lines = text.split("\r\n")
    edit(lines)
    data = "\r\n".join(lines).encode("ascii")
    return check_quarterly_file(data, layout, profile, 2025, 1, source=report)


def _set(line: str, start: int, value: str) -> str:
    return line[:start - 1] + value + line[start - 1 + len(value):]


def _messages(result) -> str:
    return " | ".join(i.message for i in result.issues.errors)


def test_check_catches_a_changed_wage_amount():
    def edit(lines):
        lines[2] = _set(lines[2], 203, "00000000001")
    result = _damaged(edit)
    assert "Total Wages: file has 0.01" in _messages(result)


def test_check_catches_the_wrong_quarter():
    def edit(lines):
        lines[3] = _set(lines[3], 197, "062025")
    assert "reporting period" in _messages(_damaged(edit))


def test_check_catches_taxable_above_total():
    def edit(lines):
        lines[2] = _set(lines[2], 214, "99999999999")
    assert "taxable UI wages are more than total" in _messages(_damaged(edit))


def test_check_catches_a_duplicated_employee():
    def edit(lines):
        lines.insert(3, lines[2])
    messages = _messages(_damaged(edit))
    assert "second RS record" in messages and "employees, source has" in messages


def test_check_catches_income_tax_data():
    def edit(lines):
        lines[2] = _set(lines[2], 276, "00000010000")
    assert "income tax data" in _messages(_damaged(edit))


def test_check_catches_a_wrong_account_number():
    def edit(lines):
        for i in range(2, len(lines) - 1):
            lines[i] = _set(lines[i], 248, "1234567")
    assert "does not match the employer's UI account" in _messages(_damaged(edit))


def test_check_catches_record_order_and_missing_final_record():
    def edit(lines):
        lines.pop()
    assert "not in the order" in _messages(_damaged(edit))


def test_ui_command_needs_no_network_and_prints_no_ssn(no_network, tmp_path, capsys):
    workbook, submitter_file, _ = example(2025)
    out = tmp_path / "ui.txt"
    code = main(["ui", "--state", "VA", "--quarter", "1", "--input", str(workbook),
                 "--submitter", str(submitter_file), "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 0
    assert out.read_bytes() == golden(2025, 1).read_bytes()
    assert "PROVISIONAL" in printed and "on the 12th" in printed
    for e in ui_report(2025, 1).employees:
        assert e.ssn in out.read_text()
        assert e.ssn not in printed
        assert f"{e.ssn[:3]}-{e.ssn[3:5]}-{e.ssn[5:]}" not in printed


def test_check_ui_command_rejects_the_wrong_quarter(capsys):
    code = main(["check-ui", str(golden(2025, 2)), "--state", "VA", "--quarter", "3"])
    assert code == 1
    assert "reporting period" in capsys.readouterr().out
