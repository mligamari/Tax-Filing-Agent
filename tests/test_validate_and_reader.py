from decimal import Decimal

from openpyxl import Workbook

from taxfiling.readers.excel import load_mapping, read_w2_workbook
from taxfiling.validate import ein_problem, ssn_problem, validate_report

from .conftest import VA_WORKBOOK


def test_ssn_rules():
    assert ssn_problem("864789322") is None
    for bad in ("000123456", "666123456", "900123456", "123004567", "123450000", "12345678", "12345678a"):
        assert ssn_problem(bad), bad


def test_ein_rules():
    assert ein_problem("543187620") is None
    assert ein_problem("073187620")
    assert ein_problem("54318762")


def test_reads_the_synthetic_workbook():
    employer, employees, issues = read_w2_workbook(VA_WORKBOOK, load_mapping("w2_standard"))
    assert len(employees) == 50  # the TOTAL row is skipped
    assert employer.ein == "543187620"
    assert employer.state_ids == {"VA": "30-543187620F-001"}
    first = employees[0]
    assert first.ssn == "237965232"
    assert first.box12 == {"D": Decimal("9642.73"), "DD": Decimal("12394.50"), "W": Decimal("2383.56"),
                           "C": Decimal("374.98")}
    assert first.retirement_plan and not first.statutory_employee
    assert first.states[0].wages == Decimal("82297.12")
    assert employees[11].box12["T"] == Decimal("9850.00")
    assert not issues.errors and not issues.warnings


def test_valid_input_has_no_errors(va_report):
    assert not validate_report(va_report).errors


def _tiny_workbook(path, ssn, zip_code):
    mapping = load_mapping("w2_standard")
    wb = Workbook()
    ws = wb.active
    ws.title = mapping["sheet"]
    headers = list(dict.fromkeys(list(mapping["columns"].values())
                                 + [h for pair in mapping["box12"] for h in pair.values()]))
    ws.append(headers)
    row = {h: None for h in headers}
    c = mapping["columns"]
    row.update({c["control_number"]: "1", c["ssn"]: ssn, c["employer_ein"]: "54-3187620",
                c["employer_name"]: "Test Co", c["employer_address"]: "1 Main St", c["employer_city"]: "Richmond",
                c["employer_state"]: "VA", c["employer_zip"]: "23230", c["first_name"]: "Ann",
                c["last_name"]: "Lee", c["address"]: "2 Oak St", c["city"]: "Norfolk", c["state"]: "VA",
                c["zip"]: zip_code, c["box1_wages"]: 1000.005})
    ws.append([row[h] for h in headers])
    wb.save(path)
    return mapping


def test_numbers_that_lost_leading_zeros_are_restored_with_a_warning(tmp_path):
    path = tmp_path / "w2.xlsx"
    mapping = _tiny_workbook(path, ssn=12345678, zip_code=2134)
    _, employees, issues = read_w2_workbook(path, mapping)
    assert employees[0].ssn == "012345678"
    assert employees[0].address.zip == "02134"
    messages = " ".join(i.message for i in issues.warnings)
    assert "SSN" in messages and "ZIP" in messages
    assert "rounded to 1000.01" in messages
    assert "12345678" not in messages  # no full SSN in messages


def test_invalid_ssn_is_an_error(va_report):
    va_report.employees[3].ssn = "666123456"
    errors = validate_report(va_report).errors
    assert len(errors) == 1 and "SSN" in errors[0].message
    assert "666123456" not in str(errors[0])


def test_social_security_wage_base_is_enforced(va_report, va_report_2026):
    """2025 base $176,100 and 2026 base $184,500, from SSA's EFW2 specification for each year."""
    va_report.employees[0].box3_ss_wages = va_report.employees[0].box5_medicare_wages = Decimal("176100.01")
    assert any("wage base 176100" in e.message for e in validate_report(va_report).errors)
    va_report_2026.employees[0].box3_ss_wages = va_report_2026.employees[0].box5_medicare_wages = Decimal("176100.01")
    assert not any("wage base" in e.message for e in validate_report(va_report_2026).errors)


def test_social_security_tax_over_the_maximum(va_report):
    va_report.employees[0].box4_ss_tax = Decimal("10918.21")  # 6.2% of 176,100 = 10,918.20
    assert any("over the 2025 maximum 10918.20" in e.message for e in validate_report(va_report).errors)


def test_medicare_wages_below_social_security_wages_is_an_error(va_report):
    """SSA lists this among the conditions that make it reject a whole file."""
    va_report.employees[0].box5_medicare_wages = va_report.employees[0].box3_ss_wages - Decimal("0.01")
    assert any("Box 5 Medicare wages are less" in e.message for e in validate_report(va_report).errors)


def test_a_year_without_federal_amounts_is_flagged(va_report):
    va_report.tax_year = 2031
    assert any("no Social Security wage base on file for 2031" in w.message
               for w in validate_report(va_report).warnings)
