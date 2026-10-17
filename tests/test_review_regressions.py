"""Regression tests for problems found in code review."""
from decimal import Decimal

from taxfiling import cli
from taxfiling.check import CheckResult, check_file
from taxfiling.formats.efw2 import build_efw2
from taxfiling.readers.excel import read_w2_workbook

from .conftest import VA_SUBMITTER, VA_WORKBOOK
from .test_check_catches_damage import errors, put
from .test_validate_and_reader import _tiny_workbook


def _write_and_check(report, layout, profile):
    text, issues = build_efw2(report, layout, profile)
    assert text is not None, [str(i) for i in issues.errors]
    return text, issues, check_file(text.encode(), layout, profile, source=report)


def test_a_cut_location_address_still_passes_the_check(va_report, layout, va_profile):
    va_report.employees[0].address.location = "Attention Accounts Payable Dept"
    _, _, result = _write_and_check(va_report, layout, va_profile)
    assert result.ok, errors(result)
    assert any("shortened" in w.message for w in result.issues.warnings)


def test_street_is_split_at_the_last_unit_word(va_report, layout, va_profile):
    va_report.employees[0].address.delivery = "100 Suite Hollow Road Apt 12"
    text, _, result = _write_and_check(va_report, layout, va_profile)
    rw = text.split("\r\n")[2]
    assert rw[65:87].rstrip() == "APT 12"
    assert rw[87:109].rstrip() == "100 SUITE HOLLOW ROAD"
    assert result.ok, errors(result)


def test_a_damaged_ssn_is_not_echoed(va_report, layout, va_profile):
    text, _ = build_efw2(va_report, layout, va_profile)
    lines = text.split("\r\n")
    lines[2] = put(lines[2], 3, "12345678O")
    result = check_file("\r\n".join(lines).encode(), layout, va_profile)
    assert "not numeric" in errors(result)
    assert "12345678" not in errors(result)


def test_no_file_is_left_behind_when_the_check_fails(tmp_path, monkeypatch, capsys):
    def failing_check(*args, **kwargs):
        result = CheckResult()
        result.issues.error("test", "forced failure")
        return result
    monkeypatch.setattr(cli, "check_file", failing_check)
    out = tmp_path / "W2 REPORT 2026.txt"
    code = cli.main(["w2", "--state", "VA", "--input", str(VA_WORKBOOK), "--submitter", str(VA_SUBMITTER),
                     "--out", str(out)])
    assert code == 1
    assert not out.exists()
    assert "No file written" in capsys.readouterr().out


def test_state_amounts_without_a_state_are_an_error(tmp_path):
    path = tmp_path / "w2.xlsx"
    mapping = _tiny_workbook(path, ssn="123-45-6789", zip_code="23230")
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    ws.cell(row=2, column=headers.index(mapping["columns"]["box16_state_wages"]) + 1, value=500)
    wb.save(path)
    _, _, issues = read_w2_workbook(path, mapping)
    assert any("Box 15" in e.message for e in issues.errors)


def test_zip_plus_4_that_lost_a_leading_zero(tmp_path):
    path = tmp_path / "w2.xlsx"
    mapping = _tiny_workbook(path, ssn="123-45-6789", zip_code=21381234)
    _, employees, issues = read_w2_workbook(path, mapping)
    assert (employees[0].address.zip, employees[0].address.zip_ext) == ("02138", "1234")
    assert any("ZIP" in w.message for w in issues.warnings)


def test_two_rs_records_for_one_employee_fail_without_source_data(va_report, layout, va_profile):
    text, _ = build_efw2(va_report, layout, va_profile)
    lines = text.split("\r\n")
    lines.insert(4, lines[3])
    assert "one per employee is required" in errors(check_file("\r\n".join(lines).encode(), layout, va_profile))


def test_rs_without_its_rw_ssn_is_caught_without_source_data(va_report, layout, va_profile):
    text, _ = build_efw2(va_report, layout, va_profile)
    lines = text.split("\r\n")
    lines[3] = put(lines[3], 10, lines[5][9:18])  # second employee's SSN in the first RS
    assert "RS SSN differs" in errors(check_file("\r\n".join(lines).encode(), layout, va_profile))


def test_box_11_split_in_the_file_is_compared_as_one_amount(va_report, layout, va_profile):
    text, _ = build_efw2(va_report, layout, va_profile)  # the writer refuses Box 11, so add it by hand
    va_report.employees[0].box11_nonqualified = Decimal("50.00")
    lines = text.split("\r\n")
    lines[2] = put(put(lines[2], 353, "00000003000"), 375, "00000002000")  # 30.00 + 20.00
    lines[-2] = put(put(lines[-2], 235, "000000000003000"), 265, "000000000002000")  # RT totals
    result = check_file("\r\n".join(lines).encode(), layout, va_profile, source=va_report)
    assert result.ok, errors(result)
    va_report.employees[0].box11_nonqualified = Decimal("49.00")
    result = check_file("\r\n".join(lines).encode(), layout, va_profile, source=va_report)
    assert "Box 11" in errors(result)


def test_employee_without_virginia_wages_is_left_out(va_report, layout, va_profile):
    va_report.employees[5].states = []
    text, issues, result = _write_and_check(va_report, layout, va_profile)
    assert text.count("\r\nRW") == 49
    assert any("left out" in w.message for w in issues.warnings)
    assert result.ok, errors(result)


def test_box_12_field_names_match_their_codes(layout):
    for f in layout["RW"].fields:
        if f.key.startswith("code_"):
            assert f"(Box 12 {f.key[5:]})" in f.name, f.key
