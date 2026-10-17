"""The checker must notice when a file is wrong. Each test damages a good file one way."""
import pytest

from taxfiling.check import check_file
from taxfiling.formats.efw2 import build_efw2


@pytest.fixture
def good(va_report, layout, va_profile):
    text, _ = build_efw2(va_report, layout, va_profile)
    return text.split("\r\n")


def run(lines, layout, va_profile, source=None):
    return check_file("\r\n".join(lines).encode("latin-1"), layout, va_profile, source=source)


def errors(result):
    return " | ".join(i.message for i in result.issues.errors)


def put(line, start, text):
    return line[: start - 1] + text + line[start - 1 + len(text):]


def test_good_file_passes(good, layout, va_profile, va_report):
    assert run(good, layout, va_profile, va_report).ok


def test_changed_wage_breaks_rt_total_and_source_match(good, layout, va_profile, va_report):
    good[2] = put(good[2], 188, "00003343515")  # first RW Box 1 + 1 cent
    result = run(good, layout, va_profile, va_report)
    assert "RT Total wages, tips and other compensation" in errors(result)
    assert "file has 33435.15, source has 33435.14" in errors(result)


def test_consistent_but_wrong_amount_is_caught_by_the_source_comparison(good, layout, va_profile, va_report):
    """Change an RS amount: no RT total covers RS, so only the source comparison can see it."""
    good[3] = put(good[3], 287, "00000110843")
    assert run(good, layout, va_profile).ok          # structure alone cannot tell
    assert "state tax (Box 17)" in errors(run(good, layout, va_profile, va_report))


def test_short_record(good, layout, va_profile):
    good[5] = good[5][:-1]
    assert "must be 512" in errors(run(good, layout, va_profile))


def test_left_justified_money_field(good, layout, va_profile):
    good[2] = put(good[2], 188, "3343514    ")
    assert "positions 188-198" in errors(run(good, layout, va_profile))


def test_right_justified_name(good, layout, va_profile):
    good[2] = put(good[2], 12, "           NOOR")
    assert "not left-justified" in errors(run(good, layout, va_profile))


def test_text_in_a_blank_area(good, layout, va_profile):
    good[2] = put(good[2], 500, "X")
    assert "must be blank" in errors(run(good, layout, va_profile))


def test_wrong_state_code(good, layout, va_profile):
    good[3] = put(good[3], 3, "37")
    assert "must be '51'" in errors(run(good, layout, va_profile))


def test_missing_rf(good, layout, va_profile):
    assert "last record must be RF" in errors(run(good[:-1], layout, va_profile))


def test_wrong_rf_count(good, layout, va_profile):
    good[-1] = put(good[-1], 8, "000000049")
    assert "RF says" in errors(run(good, layout, va_profile))


def test_records_out_of_order(good, layout, va_profile):
    good[2], good[3] = good[3], good[2]
    assert "cannot follow" in errors(run(good, layout, va_profile))


def test_non_ascii_byte(good, layout, va_profile):
    good[2] = put(good[2], 20, "é")
    assert "not ASCII" in errors(run(good, layout, va_profile))


def test_bare_lf_line_endings(good, layout, va_profile):
    result = check_file("\n".join(good).encode(), layout, va_profile)
    assert not result.ok


def test_employee_dropped_from_the_file(good, layout, va_profile, va_report):
    del good[2:4]
    assert "file has 49 employees, source has 50" in errors(run(good, layout, va_profile, va_report))


def test_ssn_swapped_between_records(good, layout, va_profile, va_report):
    ssn = good[4][2:11]
    good[2] = put(good[2], 3, ssn)
    message = errors(run(good, layout, va_profile, va_report))
    assert "does not match source" in message
    assert ssn not in message  # SSNs stay masked in messages
