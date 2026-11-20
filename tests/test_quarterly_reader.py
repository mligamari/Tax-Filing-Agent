from decimal import Decimal

import pytest
from openpyxl import Workbook

from taxfiling.model import QuarterlyReport
from taxfiling.readers.excel import load_mapping, read_w2_workbook
from taxfiling.readers.quarterly import read_quarterly_workbook

from .conftest import VA_WORKBOOK

MAPPING = load_mapping("quarterly_standard")


def test_reads_one_quarter_of_the_synthetic_workbook():
    rows, issues = read_quarterly_workbook(VA_WORKBOOK, MAPPING, 2025, 2)
    assert not issues.errors and not issues.warnings
    first = rows[0]
    assert first.ssn == "237965232" and first.last_name == "Ramirez"
    assert first.gross_wages == Decimal("26210.92")
    assert first.excess_wages == Decimal("22531.40")
    assert first.taxable_wages == Decimal("3679.52")
    assert first.employed_12th == (True, True, True)
    assert len({r.ssn for r in rows}) == len(rows)


def test_every_quarter_adds_up_to_the_sheet():
    count = 0
    for q in (1, 2, 3, 4):
        rows, issues = read_quarterly_workbook(VA_WORKBOOK, MAPPING, 2025, q)
        assert not issues.errors
        count += len(rows)
    assert count == 183  # every data row of the sheet, the TOTAL row skipped


def test_quarterly_ui_wages_match_medicare_wages_on_the_w2():
    """In the synthetic data, UI gross wages per person over the year equal W-2 Box 5 (Medicare wages,
    which, unlike Box 3, have no wage cap)."""
    _, w2s, _ = read_w2_workbook(VA_WORKBOOK, load_mapping("w2_standard"))
    yearly: dict[str, Decimal] = {}
    for q in (1, 2, 3, 4):
        for r in read_quarterly_workbook(VA_WORKBOOK, MAPPING, 2025, q)[0]:
            yearly[r.ssn] = yearly.get(r.ssn, Decimal(0)) + r.gross_wages
    assert yearly == {w.ssn: w.box5_medicare_wages for w in w2s if w.box5_medicare_wages}


def test_employed_on_12th_counts():
    employer, _, _ = read_w2_workbook(VA_WORKBOOK, load_mapping("w2_standard"))
    rows, _ = read_quarterly_workbook(VA_WORKBOOK, MAPPING, 2025, 1)
    report = QuarterlyReport(tax_year=2025, quarter=1, state="VA", employer=employer, employees=rows)
    counts = report.employed_12th_counts
    assert all(0 < c <= len(rows) for c in counts)


def _sheet(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = MAPPING["sheet"]
    headers = list(MAPPING["columns"].values())
    ws.append(headers)
    for r in rows:
        base = {"Employee SSN": "237-96-5232", "Employee First Name": "Ann", "Employee Last Name": "Lee",
                "Tax Year": 2025, "Quarter": 1, "Employed on 12th - Month 1": "Y",
                "Employed on 12th - Month 2": "Y", "Employed on 12th - Month 3": "N",
                "VA UI Gross Wages": 100, "VA UI Excess Wages": 0, "VA UI Taxable Wages": 100}
        base.update(r)
        ws.append([base.get(h) for h in headers])
    wb.save(path)
    return path


def test_rejects_wages_that_do_not_add_up(tmp_path):
    path = _sheet(tmp_path / "q.xlsx", [{"VA UI Excess Wages": 10}])
    _, issues = read_quarterly_workbook(path, MAPPING, 2025, 1)
    assert any("do not equal" in i.message for i in issues.errors)


def test_rejects_a_second_row_for_the_same_person(tmp_path):
    path = _sheet(tmp_path / "q.xlsx", [{}, {}])
    _, issues = read_quarterly_workbook(path, MAPPING, 2025, 1)
    assert any("same SSN" in i.message for i in issues.errors)
    assert all("237" not in str(i) for i in issues.items)  # SSN never printed


def test_rejects_negative_wages_and_bad_flags(tmp_path):
    path = _sheet(tmp_path / "q.xlsx", [{"VA UI Gross Wages": -5, "VA UI Taxable Wages": -5,
                                         "Employed on 12th - Month 2": "maybe"}])
    _, issues = read_quarterly_workbook(path, MAPPING, 2025, 1)
    messages = " ".join(i.message for i in issues.errors)
    assert "negative" in messages and "not Y or N" in messages


def test_empty_quarter_and_bad_quarter(tmp_path):
    path = _sheet(tmp_path / "q.xlsx", [{}])
    _, issues = read_quarterly_workbook(path, MAPPING, 2025, 3)
    assert any("no rows" in i.message for i in issues.errors)
    with pytest.raises(ValueError):
        read_quarterly_workbook(path, MAPPING, 2025, 5)


def test_rejects_an_invalid_ssn_without_printing_it(tmp_path):
    path = _sheet(tmp_path / "q.xlsx", [{"Employee SSN": "666-12-3456"}])
    _, issues = read_quarterly_workbook(path, MAPPING, 2025, 1)
    assert any("SSN first three digits" in i.message for i in issues.errors)
    assert all("3456" not in str(i) for i in issues.items)
