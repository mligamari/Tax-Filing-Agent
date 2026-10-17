"""Checks on the input data before any file is written.

Errors stop the file from being written. Warnings are printed and the file is
still written. No message contains a full SSN.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from .issues import Issues
from .model import W2, WageReport

# Federal amounts by tax year. Source: SSA 2026 COLA fact sheet (wage base),
# IRC 3101 (rates).
FEDERAL = {
    2026: {"ss_wage_base": Decimal("184500"), "ss_rate": Decimal("0.062"), "medicare_rate": Decimal("0.0145")},
}

# EIN prefixes the IRS does not issue (SSA EFW2 RA/RE edits).
INVALID_EIN_PREFIXES = {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69", "70", "78", "79", "89"}

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
    "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI AA AE AP".split()
)

NAME_CHARS = re.compile(r"^[A-Za-z][A-Za-z '\-.]*$")


def ssn_problem(ssn: str) -> str | None:
    """SSA validity rules for an SSN. Returns a reason, or None if it passes."""
    if not re.fullmatch(r"\d{9}", ssn or ""):
        return "must be exactly 9 digits"
    area, group, serial = ssn[:3], ssn[3:5], ssn[5:]
    if area == "000" or area == "666" or area[0] == "9":
        return "first three digits cannot be 000, 666 or 900-999"
    if group == "00":
        return "middle two digits cannot be 00"
    if serial == "0000":
        return "last four digits cannot be 0000"
    return None


def ein_problem(ein: str) -> str | None:
    if not re.fullmatch(r"\d{9}", ein or ""):
        return "must be exactly 9 digits"
    if ein[:2] in INVALID_EIN_PREFIXES:
        return f"prefix {ein[:2]} is not issued by the IRS"
    return None


def _check_address(addr, where: str, issues: Issues, label: str) -> None:
    if not addr.delivery:
        issues.error(where, f"{label} street address is blank")
    if not addr.city:
        issues.error(where, f"{label} city is blank")
    if addr.state not in US_STATES:
        issues.error(where, f"{label} state '{addr.state}' is not a US postal abbreviation")
    if not re.fullmatch(r"\d{5}", addr.zip or ""):
        issues.error(where, f"{label} ZIP code '{addr.zip}' is not 5 digits")
    if addr.zip_ext and not re.fullmatch(r"\d{4}", addr.zip_ext):
        issues.error(where, f"{label} ZIP+4 extension '{addr.zip_ext}' is not 4 digits")


def _check_employee(w2: W2, year: int, issues: Issues) -> None:
    where = w2.where()
    problem = ssn_problem(w2.ssn)
    if problem:
        issues.error(where, f"SSN {problem}")
    for label, value in (("first name", w2.first_name), ("last name", w2.last_name)):
        if not value:
            issues.error(where, f"{label} is blank")
        elif not NAME_CHARS.match(value):
            issues.warning(where, f"{label} '{value}' has characters other than letters, spaces, hyphens, "
                                  "apostrophes and periods")
    _check_address(w2.address, where, issues, "employee")

    fed = FEDERAL.get(year)
    if fed:
        ss_wages = w2.box3_ss_wages + w2.box7_ss_tips
        if ss_wages > fed["ss_wage_base"]:
            issues.error(where, f"Box 3 + Box 7 = {ss_wages} is over the {year} Social Security wage base "
                                f"{fed['ss_wage_base']}")
        expected_ss = (ss_wages * fed["ss_rate"]).quantize(Decimal("0.01"))
        if abs(w2.box4_ss_tax - expected_ss) > Decimal("1.00"):
            issues.warning(where, f"Box 4 Social Security tax {w2.box4_ss_tax} is not 6.2% of Box 3 + Box 7 "
                                  f"({expected_ss})")
        min_medicare = (w2.box5_medicare_wages * fed["medicare_rate"]).quantize(Decimal("0.01"))
        if w2.box6_medicare_tax + Decimal("1.00") < min_medicare:
            issues.warning(where, f"Box 6 Medicare tax {w2.box6_medicare_tax} is less than 1.45% of Box 5 "
                                  f"({min_medicare})")
    if w2.box5_medicare_wages + Decimal("0.01") < w2.box3_ss_wages + w2.box7_ss_tips:
        issues.warning(where, "Box 5 Medicare wages are less than Box 3 + Box 7; usually they are equal or more")
    if w2.box4_ss_tax and not (w2.box3_ss_wages or w2.box7_ss_tips):
        issues.error(where, "Box 4 has Social Security tax but Boxes 3 and 7 are zero")
    if w2.box6_medicare_tax and not w2.box5_medicare_wages:
        issues.error(where, "Box 6 has Medicare tax but Box 5 is zero")
    for st in w2.states:
        if st.tax and not st.wages:
            issues.error(where, f"Box 17 has {st.state} tax withheld but Box 16 wages are zero")
        if (st.wages or st.tax) and not st.employer_state_id:
            issues.error(where, f"Box 15 {st.state} employer state ID is blank")


def validate_report(report: WageReport) -> Issues:
    issues = Issues()
    s = report.submitter
    problem = ein_problem(s.ein)
    if problem:
        issues.error("submitter", f"EIN {problem}")
    if len(s.user_id) != 8 or not s.user_id.isalnum():
        issues.error("submitter", "BSO User ID must be 8 letters or digits")
    for label, value in (("name", s.name), ("contact name", s.contact.name), ("contact phone", s.contact.phone),
                         ("contact e-mail", s.contact.email)):
        if not value:
            issues.error("submitter", f"{label} is blank")
    if s.contact.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s.contact.email):
        issues.error("submitter", f"contact e-mail '{s.contact.email}' is not an e-mail address")
    if s.software_code not in ("98", "99"):
        issues.error("submitter", "software code must be 98 (in-house) or 99 (off-the-shelf)")
    if s.preparer_code not in ("A", "L", "S", "P", "O"):
        issues.error("submitter", "preparer code must be A, L, S, P or O")
    _check_address(s.address, "submitter", issues, "submitter")

    e = report.employer
    problem = ein_problem(e.ein)
    if problem:
        issues.error("employer", f"EIN {problem}")
    if not e.name:
        issues.error("employer", "name is blank")
    _check_address(e.address, "employer", issues, "employer")
    if e.kind_of_employer not in ("F", "S", "T", "Y", "N"):
        issues.error("employer", "kind of employer must be F, S, T, Y or N")
    if e.employment_code not in ("A", "H", "M", "Q", "X", "F", "R"):
        issues.error("employer", "employment code must be A, H, M, Q, X, F or R")

    if not report.employees:
        issues.error("input", "no employee rows found")
    for w2 in report.employees:
        _check_employee(w2, report.tax_year, issues)

    counts = Counter(w.ssn for w in report.employees if w.ssn)
    for ssn, n in counts.items():
        if n > 1:
            rows = ", ".join(str(w.source_row) for w in report.employees if w.ssn == ssn)
            issues.warning("input", f"the same SSN appears on {n} rows ({rows}); "
                                    "that is allowed only for separate W-2s")
    controls = Counter(w.control_number for w in report.employees if w.control_number)
    for control, n in controls.items():
        if n > 1:
            issues.warning("input", f"control number {control} appears on {n} rows")
    return issues
