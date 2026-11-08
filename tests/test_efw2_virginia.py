"""End-to-end: synthetic Virginia workbook -> EFW2 file -> round-trip check."""
from decimal import Decimal

import pytest

from taxfiling.check import check_file
from taxfiling.formats.efw2 import build_efw2

from .conftest import example, make_report


def _build(report, layout, profile):
    text, issues = build_efw2(report, layout, profile)
    assert text is not None, [str(i) for i in issues.errors]
    return text, issues


def _lines_of(text, record_type):
    return [line for line in text.split("\r\n") if line.startswith(record_type)]


@pytest.mark.parametrize("year", [2025, 2026])
def test_matches_the_committed_golden_file(year, request):
    layout = request.getfixturevalue("layout" if year == 2025 else "layout_2026")
    profile = request.getfixturevalue("va_profile" if year == 2025 else "va_profile_2026")
    text, _ = _build(make_report(year), layout, profile)
    assert text.encode("ascii") == example(year)[2].read_bytes(), \
        f"output changed; if intended, regenerate examples/va_{year}/output (see README)"


def test_file_shape(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    lines = text.split("\r\n")
    assert all(len(line) == 512 for line in lines)
    assert not text.endswith("\r\n")  # Virginia: CR/LF after every record except the last
    assert "\n" not in text.replace("\r\n", "")
    expected = ["RA", "RE"]
    for w2 in va_report.employees:
        expected += ["RW", "RO", "RS"] if "T" in w2.box12 else ["RW", "RS"]
    expected += ["RT", "RU", "RF"]
    assert [line[:2] for line in lines] == expected
    assert text.isascii() and text.upper() == text.replace("payroll@example.com", "PAYROLL@EXAMPLE.COM")


def test_virginia_fields_at_their_positions(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    lines = text.split("\r\n")
    re_, rs = lines[1], lines[3]
    assert re_[2:6] == "2025"                       # RE 3-6 tax year
    assert re_[7:16] == "543187620"                 # RE 8-16 FEIN
    assert re_[39:96].rstrip() == "BLUE RIDGE PRECISION COMPONENTS INC"
    assert rs[2:4] == "51" and rs[273:275] == "51"  # RS 3-4 and 274-275 state code
    assert rs[9:18] == "237965232"                  # RS 10-18 SSN
    assert rs[18:33].rstrip() == "LAUREN" and rs[48:68].rstrip() == "RAMIREZ"
    assert rs[247:267].rstrip() == "30543187620F001"
    assert rs[275:286] == "00008229712"             # RS 276-286 state wages 82,297.12
    assert rs[286:297] == "00000336138"             # RS 287-297 state tax 3,361.38
    assert rs[202:224] == "0" * 22                  # unemployment money fields: SSA's zero-fill rule
    assert rs[194:202].strip() == "" and rs[224:242].strip() == ""


def test_adoption_benefits_go_in_ro_and_ru(va_report, layout, va_profile):
    """Box 12 code T is the one RO amount in the 2025 example (control 00012)."""
    text, _ = _build(va_report, layout, va_profile)
    (ro,), (ru,) = _lines_of(text, "RO"), _lines_of(text, "RU")
    lines = text.split("\r\n")
    assert lines[lines.index(ro) - 1][2:11] == "388174183"   # the RO follows its employee's RW
    assert ro[55:66] == "00000985000"                        # RO 56-66 code T 9,850.00
    assert (ro[2:55] + ro[66:]).replace("0", "").strip() == ""  # nothing else in it
    assert ru[2:9] == "0000001"                              # RU 3-9 one RO record
    assert ru[69:84] == "000000000985000"                    # RU 70-84 total code T
    rw = lines[lines.index(ro) - 1]
    assert rw[209:220] == "00012442353"                      # Box 3 includes the benefit
    assert rw[187:198] == "00010752319"                      # Box 1 does not


def test_round_trip_check_passes_and_compares_every_employee(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    result = check_file(text.encode(), layout, va_profile, source=va_report)
    assert result.ok, [str(i) for i in result.issues.errors]
    assert result.employees_compared == 50
    assert result.totals["total_box1_wages"] == sum(w.box1_wages for w in va_report.employees)
    assert result.totals["total_code_T"] == Decimal("9850.00")
    assert result.totals["state_tax"] == sum(w.states[0].tax for w in va_report.employees)


def test_2026_overtime_goes_in_ro(va_report_2026, layout_2026, va_profile_2026):
    text, issues = _build(va_report_2026, layout_2026, va_profile_2026)
    with_tt = [w for w in va_report_2026.employees if "TT" in w.box12]
    ros = _lines_of(text, "RO")
    assert len(ros) == len(with_tt) == 6
    assert [Decimal(int(ro[242:253])) / 100 for ro in ros] == [w.box12["TT"] for w in with_tt]
    (ru,) = _lines_of(text, "RU")
    assert Decimal(int(ru[324:339])) / 100 == sum(w.box12["TT"] for w in with_tt)
    assert not any("TT" in i.message for i in issues.items)
    assert check_file(text.encode(), layout_2026, va_profile_2026, source=va_report_2026).ok


def test_2026_codes_a_and_b_have_their_own_fields(va_report_2026, layout_2026, va_profile_2026):
    w2 = va_report_2026.employees[0]
    w2.box12.update({"A": Decimal("10.00"), "B": Decimal("2.50")})
    text, _ = _build(va_report_2026, layout_2026, va_profile_2026)
    ro = text.split("\r\n")[3]
    assert ro[:2] == "RO"
    assert ro[22:33] == "0" * 11                   # 23-33 is for 2025 and earlier
    assert ro[154:165] == "00000001000" and ro[165:176] == "00000000250"
    assert check_file(text.encode(), layout_2026, va_profile_2026, source=va_report_2026).ok


def test_2025_codes_a_and_b_share_one_field(va_report, layout, va_profile):
    va_report.employees[0].box12.update({"A": Decimal("10.00"), "B": Decimal("2.50")})
    text, _ = _build(va_report, layout, va_profile)
    ro = text.split("\r\n")[3]
    assert ro[:2] == "RO" and ro[22:33] == "00000001250"
    assert check_file(text.encode(), layout, va_profile, source=va_report).ok


def test_box_8_allocated_tips_go_in_ro(va_report, layout, va_profile):
    va_report.employees[1].box8_allocated_tips = Decimal("321.09")
    text, _ = _build(va_report, layout, va_profile)
    lines = text.split("\r\n")
    assert lines[5][:2] == "RO" and lines[5][11:22] == "00000032109"
    assert check_file(text.encode(), layout, va_profile, source=va_report).ok


def test_codes_without_an_efw2_field_are_warned_about(va_report, layout, va_profile):
    """SSA's 2025 Appendix E: codes J, K, L and P "do not relate to an EFW2 field"."""
    va_report.employees[0].box12["J"] = Decimal("400.00")
    text, issues = _build(va_report, layout, va_profile)
    assert any("code J (Nontaxable sick pay)" in w.message and "no field" in w.message for w in issues.warnings)
    assert text.split("\r\n")[3][:2] == "RS"  # no RO record for it
    result = check_file(text.encode(), layout, va_profile, source=va_report)
    assert result.ok and any("Box 12 J" in w.message for w in result.issues.warnings)


def test_a_code_with_no_amount_is_not_reported(va_report, layout, va_profile):
    va_report.employees[0].box12["J"] = Decimal("0.00")
    _, issues = _build(va_report, layout, va_profile)
    assert not any("code J" in i.message for i in issues.items)


def test_a_2026_code_in_a_2025_file_is_an_error(va_report, layout, va_profile):
    va_report.employees[0].box12["TT"] = Decimal("100.00")
    text, issues = build_efw2(va_report, layout, va_profile)
    assert text is None
    assert any("'TT' is not a 2025 Form W-2 code" in e.message for e in issues.errors)


def test_long_street_moves_the_apartment_to_the_location_address(va_report, layout, va_profile):
    text, issues = _build(va_report, layout, va_profile)
    (rw,) = [line for line in _lines_of(text, "RW") if line[2:11] == "711425455"]  # 146 Midlothian Tpke Apt 3C
    assert rw[65:87].rstrip() == "APT 3C"
    assert rw[87:109].rstrip() == "146 MIDLOTHIAN TPKE"
    assert not any("Midlothian" in i.message for i in issues.items)


def test_box_11_stops_the_file(va_report, layout, va_profile):
    va_report.employees[1].box11_nonqualified = Decimal("50.00")
    text, issues = build_efw2(va_report, layout, va_profile)
    assert text is None
    assert any("Box 11" in e.message for e in issues.errors)


def test_box_14b_is_not_in_a_2025_file(va_report, layout, va_profile):
    va_report.employees[0].box14b_tipped_occupation = "101"
    text, issues = _build(va_report, layout, va_profile)
    assert any("Box 14b" in w.message and "2026" in w.message for w in issues.warnings)


def test_box_14b_is_written_in_2026(va_report_2026, layout_2026, va_profile_2026):
    w2 = va_report_2026.employees[0]
    w2.box12["TP"] = Decimal("1500.00")
    w2.box14b_tipped_occupation = "101, 205"
    text, issues = _build(va_report_2026, layout_2026, va_profile_2026)
    rw = text.split("\r\n")[2]
    assert rw[489:495] == "101205" and rw[495:].strip() == ""
    assert not any("Box 14b" in w.message or "TP" in w.message for w in issues.warnings)
    assert check_file(text.encode(), layout_2026, va_profile_2026, source=va_report_2026).ok
    w2.box14b_tipped_occupation = "1O1"
    text, issues = build_efw2(va_report_2026, layout_2026, va_profile_2026)
    assert text is None and any("three-digit" in e.message for e in issues.errors)


def test_bad_virginia_account_number_stops_the_file(va_report, layout, va_profile):
    for w in va_report.employees:
        w.states[0].employer_state_id = "12-345"
    text, issues = build_efw2(va_report, layout, va_profile)
    assert text is None
    assert any("Virginia format" in e.message for e in issues.errors)


def test_name_too_long_is_an_error_not_a_silent_cut(va_report, layout, va_profile):
    va_report.employees[0].last_name = "Featherstonehaugh-Montgomery"
    text, issues = build_efw2(va_report, layout, va_profile)
    assert text is None
    assert any("Last Name" in e.message for e in issues.errors)
