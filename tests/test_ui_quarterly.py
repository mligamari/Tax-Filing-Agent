"""Quarterly unemployment-insurance (UI) file for the Virginia Employment Commission (VEC), in the
EFW2 shape of VEC's own sample file: RA, RE, one RS per employee, VEC's RT totals record, RF."""
from pathlib import Path

import pytest

from taxfiling.check_quarterly import check_quarterly_file
from taxfiling.cli import main
from taxfiling.config import apply_employer_settings, load_submitter
from taxfiling.formats.efw2_quarterly import build_efw2_quarterly, reporting_period
from taxfiling.readers.quarterly import load_quarterly_report
from taxfiling.states import profile_layout, report_profile

from .conftest import ROOT, example

VEC_SAMPLE = ROOT / "tests" / "data" / "vec_sample_efw2_quarterly.txt"


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
    layout = profile_layout(profile)
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


def test_file_shape_and_fields_at_their_positions():
    text, issues, report, *_ = build(2025, 2)
    assert text.endswith("\n") and "\r" not in text      # LF after every record, as in VEC's sample
    lines = text[:-1].split("\n")
    assert [line[:2] for line in lines] == ["RA", "RE"] + ["RS"] * len(report.employees) + ["RT", "RF"]
    assert all(len(line) == 512 for line in lines)
    rs = lines[2]
    first = report.employees[0]
    assert rs[2:4] == "51" and rs[4:9] == "B    " and rs[307] == "B"   # as in VEC's sample
    assert rs[9:18] == first.ssn
    assert rs[196:202] == "062025"                           # reporting period
    assert rs[202:213] == f"{int(first.gross_wages * 100):011d}"
    assert rs[213:224].strip() == ""                         # VEC's sample leaves per-employee taxable blank
    assert rs[247:267] == "00000000000000918274"             # 10-digit VEC account, zero-filled to 20
    assert rs[273:].strip(" B") == ""                        # no income tax data, blank not zeros
    rt = lines[-2]
    emps = report.employees
    assert rt[2:9] == f"{len(emps):07d}" and rt[9:13] == " 00 "
    assert rt[13:27] == f"{int(sum(e.gross_wages for e in emps) * 100):014d}"
    assert rt[28:42] == f"{int(sum(e.excess_wages for e in emps) * 100):014d}"
    assert rt[43:57] == f"{int(sum(e.taxable_wages for e in emps) * 100):014d}"
    assert [rt[58:65], rt[66:73], rt[74:81]] == [f"{c:07d}" for c in report.employed_12th_counts]
    assert rt[81:].strip() == ""
    assert lines[-1][7:16] == "000000000"                    # RF: no RW records
    assert lines[1][2:6] == "2025"


def test_same_shape_as_vecs_sample_file():
    """In VEC's sample RS, RT and RF records, every position outside the name and address text is
    blank in ours exactly where it is blank in the sample: the same numbers in the same places."""
    sample = {line[:2]: line for line in VEC_SAMPLE.read_text().split("\n") if line}
    text, _, _, layout, _ = build(2025, 1)
    text_fields = {i for f in layout["RS"].fields if f.type == "A"
                   and f.key not in ("taxing_entity_code", "tax_type_code", "state_employer_account")
                   for i in range(f.start, f.end + 1)}
    ours = {}
    for line in text[:-1].split("\n"):
        ours.setdefault(line[:2], line)
    assert list(sample) == ["RA", "RE", "RS", "RT", "RF"]
    for rt in ("RS", "RT", "RF"):
        for i, (a, b) in enumerate(zip(sample[rt], ours[rt]), 1):
            if rt == "RS" and i in text_fields:
                continue
            assert (a == " ") == (b == " "), f"{rt} position {i}: sample {a!r}, ours {b!r}"


def test_account_must_be_ten_digits():
    report = ui_report(2025, 1)
    report.employer.ui_accounts["VA"] = "0918274"
    text, issues, *_ = build(2025, 1, report)
    assert text is None
    assert any("10 digits" in i.message for i in issues.errors)


def test_names_with_punctuation_are_flagged():
    _, issues, *_ = build(2025, 1)
    assert any("O'Brien" in w.message and "letters" in w.message for w in issues.warnings)


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
    lines = text[:-1].split("\n")
    edit(lines)
    data = ("\n".join(lines) + "\n").encode("ascii")
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


def test_check_catches_taxable_wages_where_vec_has_none():
    def edit(lines):
        lines[2] = _set(lines[2], 214, "00000000100")
    assert "taxable UI wages are filled in" in _messages(_damaged(edit))


def test_check_catches_wrong_totals_and_counts():
    def edit(lines):
        lines[-2] = _set(lines[-2], 14, "00000000000001")    # total wages
        lines[-2] = _set(lines[-2], 59, "0000001")           # month 1 employees
    messages = _messages(_damaged(edit))
    assert "RT total wages" in messages and "month 1 employee count" in messages


def test_check_catches_a_wrong_employee_count():
    def edit(lines):
        lines[-2] = _set(lines[-2], 3, "0000999")
    assert "RT says 0000999 employees" in _messages(_damaged(edit))


def test_check_catches_crlf_line_endings():
    text, _, report, layout, profile = build(2025, 1)
    data = text.replace("\n", "\r\n").encode("ascii")
    result = check_quarterly_file(data, layout, profile, 2025, 1, source=report)
    assert any("stray CR" in i.message for i in result.issues.errors)


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
        for i in range(2, len(lines) - 2):
            lines[i] = _set(lines[i], 248, "00000000001234567890")
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
    assert "PROVISIONAL" in printed and "on the 12th" in printed and "in the RT record" in printed
    for e in ui_report(2025, 1).employees:
        assert e.ssn in out.read_bytes().decode("ascii")
        assert e.ssn not in printed
        assert f"{e.ssn[:3]}-{e.ssn[3:5]}-{e.ssn[5:]}" not in printed


def test_check_ui_command_rejects_the_wrong_quarter(capsys):
    code = main(["check-ui", str(golden(2025, 2)), "--state", "VA", "--quarter", "3"])
    assert code == 1
    assert "reporting period" in capsys.readouterr().out
