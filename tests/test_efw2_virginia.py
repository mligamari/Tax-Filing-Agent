"""End-to-end: synthetic Virginia workbook -> EFW2 file -> round-trip check."""
from decimal import Decimal

from taxfiling.check import check_file
from taxfiling.formats.efw2 import build_efw2

from .conftest import VA_GOLDEN


def _build(va_report, layout, va_profile):
    text, issues = build_efw2(va_report, layout, va_profile)
    assert text is not None, [str(i) for i in issues.errors]
    return text, issues


def test_matches_the_committed_golden_file(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    assert text.encode("ascii") == VA_GOLDEN.read_bytes(), \
        "output changed; if intended, regenerate examples/va_2026/output (see README)"


def test_file_shape(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    lines = text.split("\r\n")
    assert all(len(line) == 512 for line in lines)
    assert not text.endswith("\r\n")  # Virginia: CR/LF after every record except the last
    assert "\n" not in text.replace("\r\n", "")
    types = [line[:2] for line in lines]
    assert types[:2] == ["RA", "RE"] and types[-2:] == ["RT", "RF"]
    assert types[2:-2] == ["RW", "RS"] * 50
    assert text.isascii() and text.upper() == text.replace("payroll@example.com", "PAYROLL@EXAMPLE.COM")


def test_virginia_fields_at_their_positions(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    lines = text.split("\r\n")
    re_, rs = lines[1], lines[3]
    assert re_[2:6] == "2026"                       # RE 3-6 tax year
    assert re_[7:16] == "543187620"                 # RE 8-16 FEIN
    assert re_[39:96].rstrip() == "BLUE RIDGE PRECISION COMPONENTS INC"
    assert rs[2:4] == "51" and rs[273:275] == "51"  # RS 3-4 and 274-275 state code
    assert rs[9:18] == "864789322"                  # RS 10-18 SSN
    assert rs[247:267].rstrip() == "30543187620F001"
    assert rs[275:286] == "00003343514"             # RS 276-286 state wages 33,435.14
    assert rs[286:297] == "00000110842"             # RS 287-297 state tax 1,108.42


def test_round_trip_check_passes_and_compares_every_employee(va_report, layout, va_profile):
    text, _ = _build(va_report, layout, va_profile)
    result = check_file(text.encode(), layout, va_profile, source=va_report)
    assert result.ok, [str(i) for i in result.issues.errors]
    assert result.employees_compared == 50
    assert result.totals["total_box1_wages"] == sum(w.box1_wages for w in va_report.employees)
    assert result.totals["state_tax"] == sum(w.states[0].tax for w in va_report.employees)


def test_amounts_the_file_cannot_carry_yet_are_reported(va_report, layout, va_profile):
    _, issues = _build(va_report, layout, va_profile)
    tt = [w for w in va_report.employees if "TT" in w.box12]
    flagged = [i for i in issues.warnings if "code TT" in i.message]
    assert len(flagged) == len(tt) == 6


def test_long_street_moves_the_apartment_to_the_location_address(va_report, layout, va_profile):
    text, issues = _build(va_report, layout, va_profile)
    rw = text.split("\r\n")[2 + 2 * 2]  # third employee: "7406 Monument Ave Apt 15A"
    assert rw[65:87].rstrip() == "APT 15A"
    assert rw[87:109].rstrip() == "7406 MONUMENT AVE"
    assert not any("7406" in i.message for i in issues.items)


def test_box_14b_and_box_11_stop_the_file(va_report, layout, va_profile):
    va_report.employees[0].box14b_tipped_occupation = "101"
    va_report.employees[1].box11_nonqualified = Decimal("50.00")
    text, issues = build_efw2(va_report, layout, va_profile)
    assert text is None
    messages = " ".join(e.message for e in issues.errors)
    assert "Box 14b" in messages and "Box 11" in messages


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
    assert any("last name" in e.message for e in issues.errors)
