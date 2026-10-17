"""EFW2 (SSA W-2 wage file) builder.

This module decides which value goes in which field. Positions, widths and
padding come from the layout table (layouts/efw2_ty2026.json) and are applied by
taxfiling.layout, so a new tax year is a new table, not new code.

Record order written: RA, RE, then for each employee RW followed by the state's
RS record, then RT and RF. RO/RU (Box 8, Box 12 codes kept only in RO) are not
written yet: their TY2026 layout has not been verified. Any such amount in the
input is reported as a warning naming the employee and code.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..issues import Issues
from ..layout import Layout, LayoutError, encode_record
from ..model import W2, Address, WageReport

# Box 12 codes that have their own field in the RW record, and the RW field key.
RW_BOX12 = {
    "C": "code_C", "D": "code_D", "E": "code_E", "F": "code_F", "G": "code_G", "H": "code_H",
    "Q": "code_Q", "V": "code_V", "W": "code_W", "Y": "code_Y", "AA": "code_AA", "BB": "code_BB",
    "DD": "code_DD", "FF": "code_FF",
}
# Box 12 codes reported in the RO record (or nowhere in EFW2), which this writer does not produce yet.
RO_BOX12 = {"A", "B", "J", "K", "L", "M", "N", "P", "R", "S", "T", "Z", "EE", "GG", "HH", "II", "TA", "TP", "TT"}

UNIT_WORDS = re.compile(r"\s+((?:APT|APARTMENT|STE|SUITE|UNIT|RM|ROOM|FL|FLOOR|BLDG|#)\b\.?\s*\S.*)$", re.I)


class Efw2Builder:
    def __init__(self, layout: Layout, profile: dict):
        self.layout = layout
        self.profile = profile
        self.issues = Issues()
        self.records: list[tuple[str, dict[str, Any]]] = []
        self._people: dict[int, dict[str, Any]] = {}

    # ---- helpers -------------------------------------------------------
    def _fit(self, value: str, record: str, key: str, where: str, *, truncate: bool = False) -> str:
        width = self.layout[record][key].length
        value = (value or "").strip()
        if len(value) <= width:
            return value
        if truncate:
            cut = value[:width].rstrip()
            self.issues.warning(where, f"{record} {self.layout[record][key].name}: '{value}' is longer than "
                                       f"{width} characters; written as '{cut.upper()}'")
            return cut
        self.issues.error(where, f"{record} {self.layout[record][key].name}: '{value}' is longer than "
                                 f"{width} characters")
        return value[:width]

    def _address(self, addr: Address, record: str, prefix: str, where: str) -> dict[str, str]:
        """Street goes in the delivery address. A unit (Apt, Suite...) that does not fit moves to
        the location address, which EFW2 defines for suite and room numbers."""
        delivery, location = addr.delivery.strip(), addr.location.strip()
        width = self.layout[record][f"{prefix}delivery_address"].length
        if len(delivery) > width and not location:
            m = UNIT_WORDS.search(delivery)
            if m:
                delivery, location = delivery[: m.start()].strip(), m.group(1).strip()
        return {
            f"{prefix}location_address": self._fit(location, record, f"{prefix}location_address", where, truncate=True),
            f"{prefix}delivery_address": self._fit(delivery, record, f"{prefix}delivery_address", where, truncate=True),
            f"{prefix}city": self._fit(addr.city, record, f"{prefix}city", where, truncate=True),
            f"{prefix}state": addr.state,
            f"{prefix}zip": addr.zip,
            f"{prefix}zip_ext": addr.zip_ext,
        }

    def _add(self, record_type: str, values: dict[str, Any], where: str) -> None:
        try:
            encode_record(self.layout[record_type], values, self.layout.record_length)
        except LayoutError as exc:
            self.issues.error(where, str(exc))
        self.records.append((record_type, values))

    # ---- records -------------------------------------------------------
    def ra(self, report: WageReport) -> None:
        s = report.submitter
        values = {
            "submitter_ein": s.ein, "user_id": s.user_id, "resub_indicator": "1" if s.resub else "0",
            "resub_wfid": s.resub_wfid, "software_code": s.software_code,
            "company_name": self._fit(s.name, "RA", "company_name", "submitter"),
            "submitter_name": self._fit(s.name, "RA", "submitter_name", "submitter"),
            "contact_name": self._fit(s.contact.name, "RA", "contact_name", "submitter"),
            "contact_phone": s.contact.phone, "contact_phone_ext": s.contact.phone_ext,
            "contact_email": s.contact.email, "contact_fax": s.contact.fax, "preparer_code": s.preparer_code,
        }
        values.update(self._address(s.address, "RA", "company_", "submitter"))
        values.update(self._address(s.address, "RA", "submitter_", "submitter"))
        self._add("RA", values, "submitter")

    def re(self, report: WageReport) -> None:
        e = report.employer
        values = {
            "tax_year": str(report.tax_year), "employer_ein": e.ein,
            "terminating_business": "1" if e.terminating_business else "0",
            "employer_name": self._fit(e.name, "RE", "employer_name", "employer"),
            "kind_of_employer": e.kind_of_employer, "employment_code": e.employment_code,
            "third_party_sick_pay": "0",
            "employer_contact_name": self._fit(e.contact.name, "RE", "employer_contact_name", "employer"),
            "employer_contact_phone": e.contact.phone, "employer_contact_phone_ext": e.contact.phone_ext,
            "employer_contact_fax": e.contact.fax, "employer_contact_email": e.contact.email,
        }
        values.update(self._address(e.address, "RE", "employer_", "employer"))
        self._add("RE", values, "employer")

    def _person(self, w2: W2, record: str, where: str) -> dict[str, Any]:
        """Name and address fields, shared by RW and RS (same widths), worked out once per employee."""
        if id(w2) in self._people:
            return dict(self._people[id(w2)])
        values = {
            "ssn": w2.ssn,
            "first_name": self._fit(w2.first_name, record, "first_name", where),
            "middle_name": self._fit(w2.middle_name, record, "middle_name", where),
            "last_name": self._fit(w2.last_name, record, "last_name", where),
            "suffix": self._fit(w2.suffix, record, "suffix", where),
        }
        values.update(self._address(w2.address, record, "", where))
        self._people[id(w2)] = values
        return dict(values)

    def rw(self, w2: W2) -> None:
        where = w2.where()
        values = self._person(w2, "RW", where)
        for key in ("box1_wages", "box2_fed_tax", "box3_ss_wages", "box4_ss_tax", "box5_medicare_wages",
                    "box6_medicare_tax", "box7_ss_tips", "box10_dependent_care"):
            values[key] = getattr(w2, key)
        for key in RW_BOX12.values():
            values[key] = Decimal(0)
        values["box11_nonqual_457"] = values["box11_nonqual_not_457"] = Decimal(0)
        if w2.box11_nonqualified:
            self.issues.error(where, f"Box 11 {w2.box11_nonqualified}: EFW2 splits Box 11 into section 457 and "
                                     "non-457 amounts; the input does not say which, so it is not written")
        for code, amount in w2.box12.items():
            if code in RW_BOX12:
                values[RW_BOX12[code]] = amount
            elif code in RO_BOX12:
                self.issues.warning(where, f"Box 12 code {code} ({amount}) belongs in the RO record, which is "
                                           "not written yet (TY2026 layout unverified). Virginia does not read RO.")
            else:
                self.issues.error(where, f"Box 12 code '{code}' is not a valid W-2 code")
        if w2.box8_allocated_tips:
            self.issues.warning(where, f"Box 8 allocated tips ({w2.box8_allocated_tips}) belong in the RO record, "
                                       "which is not written yet")
        if w2.box14b_tipped_occupation:
            self.issues.error(where, "Box 14b tipped occupation code is present, but its TY2026 RW positions are "
                                     "not verified yet, so it cannot be written")
        values["statutory_employee"] = "1" if w2.statutory_employee else "0"
        values["retirement_plan"] = "1" if w2.retirement_plan else "0"
        values["third_party_sick_pay"] = "1" if w2.third_party_sick_pay else "0"
        self._add("RW", values, where)

    def rs(self, w2: W2) -> None:
        where = w2.where()
        state = self.profile["state"]
        lines = [s for s in w2.states if s.state == state]
        for other in w2.states:
            if other.state != state:
                self.issues.warning(where, f"Box 15 {other.state} wages are not part of a {state} file; skipped")
        if not lines:
            self.issues.warning(where, f"no {state} wages in Box 15-17, so no RS record is written")
            return
        if len(lines) > 1:
            self.issues.error(where, f"{len(lines)} separate {state} state lines; combine them into one")
        st = lines[0]
        account = re.sub(r"[\s\-]", "", st.employer_state_id).upper()
        rule = self.profile.get("account_number")
        if rule and not re.fullmatch(rule["pattern"], account):
            self.issues.error(where, f"Box 15 state ID '{st.employer_state_id}' does not match the "
                                     f"{self.profile['state_name']} format: {rule['description']}")
        values = self._person(w2, "RS", where)
        values.update(self.profile.get("state_record_values", {}))
        values.update({"state_employer_account": account, "state_wages": st.wages, "state_tax": st.tax})
        self._add("RS", values, where)

    def rt(self, n_rw: int, rw_values: list[dict]) -> None:
        values: dict[str, Any] = {"rw_count": str(n_rw)}
        for f in self.layout["RT"].fields:
            if f.type == "M":
                values[f.key] = sum((v.get(f.sum_of, Decimal(0)) for v in rw_values), Decimal(0)) \
                    if f.sum_of else Decimal(0)
        self._add("RT", values, "totals")

    def rf(self, n_rw: int) -> None:
        self._add("RF", {"rw_count": str(n_rw)}, "final")

    def build(self, report: WageReport) -> list[tuple[str, dict[str, Any]]]:
        if self.layout.tax_year and report.tax_year != self.layout.tax_year:
            self.issues.error("input", f"tax year {report.tax_year} does not match the layout table "
                                       f"({self.layout.tax_year})")
        self.ra(report)
        self.re(report)
        employee_records = self.profile.get("employee_records", ["RW"])
        for w2 in report.employees:
            self.rw(w2)
            if "RS" in employee_records:
                self.rs(w2)
        rw_values = [v for rt, v in self.records if rt == "RW"]
        self.rt(len(rw_values), rw_values)
        self.rf(len(rw_values))
        return self.records


def render(records: list[tuple[str, dict[str, Any]]], layout: Layout, final_line_ending: bool = True) -> str:
    lines = [encode_record(layout[rt], values, layout.record_length) for rt, values in records]
    text = layout.line_ending.join(lines)
    return text + layout.line_ending if final_line_ending else text


def build_efw2(report: WageReport, layout: Layout, profile: dict) -> tuple[str | None, Issues]:
    """Returns (file text, issues). File text is None when there are errors."""
    builder = Efw2Builder(layout, profile)
    records = builder.build(report)
    if builder.issues.errors:
        return None, builder.issues
    return render(records, layout, profile.get("final_line_ending", True)), builder.issues
