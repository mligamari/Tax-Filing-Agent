"""EFW2 (SSA W-2 wage file) builder.

This module decides which value goes in which field. Positions, widths and
padding come from the layout table for the tax year (layouts/efw2_ty2025.json,
efw2_ty2026.json), which is built from SSA's own specification text, and are
applied by taxfiling.layout. A new tax year is a new table, not new code.

Records written: RA, RE, then for each employee RW, an RO when the employee has
amounts that SSA puts in the RO record (Box 8, and Box 12 codes such as T or
TT), the state's RS record, then RT, an RU when any RO was written, and RF.

Which Box 12 code goes in which field comes from the layout: fields named
code_<CODE> (code_A_B holds codes A and B added together), checked against the
spec's Appendix E when the table is built. A code that SSA's file has no field
for in that year (J, K, L and P before 2026) is reported as a warning; a code
that is not a W-2 code for that year (TT in 2025) is an error.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..issues import Issues
from ..layout import Layout, LayoutError, encode_record
from ..model import W2, Address, WageReport
from ..states import in_state_file

# Box 12 codes on the W-2 form in years whose layout has no Appendix E chart (fallback only).
W2_BOX12 = {"A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "V", "W",
            "Y", "Z", "AA", "BB", "DD", "EE", "FF", "GG", "HH", "II"}


def box12_codes_in(key: str) -> list[str]:
    """code_D -> ["D"]; code_A_B -> ["A", "B"]; any other key -> []."""
    return key[5:].split("_") if key.startswith("code_") else []

# Where a secondary unit (Apt 4B, Suite 200, # 12) starts in a street address.
UNIT_START = re.compile(r"\s+(?=(?:(?:APT|APARTMENT|STE|SUITE|UNIT|RM|ROOM|FL|FLOOR|BLDG|LOT|TRLR|SPC|DEPT)\b|#))",
                        re.I)


class Efw2Builder:
    def __init__(self, layout: Layout, profile: dict):
        self.layout = layout
        self.profile = profile
        self.issues = Issues()
        self.records: list[tuple[str, dict[str, Any]]] = []
        self._people: dict[int, dict[str, Any]] = {}
        self.year = layout.tax_year
        # Box 12 code -> (record type, field key). Codes A and B share one field before 2026.
        self.box12_fields: dict[str, tuple[str, str]] = {}
        for rt in ("RW", "RO"):
            if rt in layout.records:
                for f in layout[rt].fields:
                    for code in box12_codes_in(f.key):
                        self.box12_fields[code] = (rt, f.key)
        self.employee_records = profile.get("employee_records", ["RW", "RO"])

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
            starts = list(UNIT_START.finditer(delivery))
            if starts:  # split at the last unit word, so "100 Suite Hollow Rd Apt 12" keeps its street
                m = starts[-1]
                delivery, location = delivery[: m.start()].strip(), delivery[m.end():].strip()
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

    def _box12(self, w2: W2, record: str) -> dict[str, Decimal]:
        """Box 12 amounts for one record, by field key: every code field present, zero if unused."""
        values: dict[str, Decimal] = {}
        for code, (rt, key) in self.box12_fields.items():
            if rt == record:
                values[key] = values.get(key, Decimal(0)) + w2.box12.get(code, Decimal(0))
        return values

    def _note_unwritten(self, w2: W2) -> None:
        """Errors and warnings for amounts in the input that the file has no place for."""
        where, year = w2.where(), self.year
        for code, amount in w2.box12.items():
            if code in self.box12_fields:
                rt, _ = self.box12_fields[code]
                if rt == "RO" and "RO" not in self.employee_records and amount:
                    self.issues.warning(where, f"Box 12 code {code} ({amount}) belongs in the RO record, which "
                                               f"this {self.profile['state_name']} profile leaves out")
                continue
            listed = self.layout.box12_codes.get(code)
            if listed is not None or (not self.layout.box12_codes and code in W2_BOX12):
                if amount:
                    about = f" ({listed['description']})" if listed else ""
                    self.issues.warning(where, f"Box 12 code {code}{about} {amount}: SSA's {year} EFW2 file has "
                                               "no field for this code, so it is not in the file")
            else:
                self.issues.error(where, f"Box 12 code '{code}' is not a {year} Form W-2 code")
        ro = self.layout.records.get("RO")
        if w2.box8_allocated_tips and (ro is None or "box8_allocated_tips" not in ro):
            self.issues.warning(where, f"Box 8 allocated tips ({w2.box8_allocated_tips}) have no field in this "
                                       "layout and are not written")
        if w2.box14b_tipped_occupation and "tipped_occupation_primary" not in self.layout["RW"]:
            self.issues.warning(where, f"Box 14b tipped occupation code '{w2.box14b_tipped_occupation}' is not part "
                                       f"of SSA's {year} EFW2 file (it starts with tax year 2026); not written")

    def _tipped_occupation(self, w2: W2, values: dict[str, Any]) -> None:
        """2026 and later: Box 14b codes go in RW 490-495 (primary, secondary)."""
        if "tipped_occupation_primary" not in self.layout["RW"]:
            return
        where = w2.where()
        text = (w2.box14b_tipped_occupation or "").strip()
        codes = [c for c in re.split(r"[\s,;/]+", text) if c]
        if text and (len(codes) > 2 or not all(re.fullmatch(r"\d{3}", c) for c in codes)):
            self.issues.error(where, f"Box 14b '{text}' must be one or two three-digit Treasury tipped occupation "
                                     "codes")
            return
        has_tp = bool(w2.box12.get("TP"))
        if codes and not has_tp:
            self.issues.warning(where, "Box 14b has a tipped occupation code but Box 12 has no code TP; SSA asks "
                                       "for the code when code TP (cash tips reported to the employer) is present")
        if has_tp and not codes:
            self.issues.warning(where, "Box 12 code TP is present but Box 14b has no Treasury tipped occupation "
                                       "code; SSA asks for one")
        values["tipped_occupation_primary"] = codes[0] if codes else None
        values["tipped_occupation_secondary"] = codes[1] if len(codes) > 1 else None

    def rw(self, w2: W2) -> None:
        where = w2.where()
        values = self._person(w2, "RW", where)
        for key in ("box1_wages", "box2_fed_tax", "box3_ss_wages", "box4_ss_tax", "box5_medicare_wages",
                    "box6_medicare_tax", "box7_ss_tips", "box10_dependent_care"):
            values[key] = getattr(w2, key)
        values.update(self._box12(w2, "RW"))
        values["box11_nonqual_457"] = values["box11_nonqual_not_457"] = Decimal(0)
        if w2.box11_nonqualified:
            self.issues.error(where, f"Box 11 {w2.box11_nonqualified}: EFW2 splits Box 11 into section 457 and "
                                     "non-457 amounts; the input does not say which, so it is not written")
        self._tipped_occupation(w2, values)
        values["statutory_employee"] = "1" if w2.statutory_employee else "0"
        values["retirement_plan"] = "1" if w2.retirement_plan else "0"
        values["third_party_sick_pay"] = "1" if w2.third_party_sick_pay else "0"
        self._add("RW", values, where)

    def ro(self, w2: W2) -> None:
        """The optional employee record: written only when it would hold an amount (SSA 3.2.3)."""
        if "RO" not in self.layout.records or "RO" not in self.employee_records:
            return
        values: dict[str, Any] = self._box12(w2, "RO")
        if "box8_allocated_tips" in self.layout["RO"]:
            values["box8_allocated_tips"] = w2.box8_allocated_tips
        if any(values.values()):
            self._add("RO", values, w2.where())

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

    def _totals(self, record: str, count_key: str, rows: list[dict]) -> None:
        values: dict[str, Any] = {count_key: str(len(rows))}
        for f in self.layout[record].fields:
            if f.type == "M":
                values[f.key] = sum((v.get(f.sum_of) or Decimal(0) for v in rows), Decimal(0)) \
                    if f.sum_of else Decimal(0)
        self._add(record, values, "totals")

    def rf(self, n_rw: int) -> None:
        self._add("RF", {"rw_count": str(n_rw)}, "final")

    def build(self, report: WageReport) -> list[tuple[str, dict[str, Any]]]:
        if self.layout.tax_year and report.tax_year != self.layout.tax_year:
            self.issues.error("input", f"tax year {report.tax_year} does not match the layout table "
                                       f"({self.layout.tax_year})")
        self.ra(report)
        self.re(report)
        for w2 in report.employees:
            if not in_state_file(w2, self.profile):
                self.issues.warning(w2.where(), f"no {self.profile['state_name']} wages in Box 15-17, so this "
                                                f"employee is left out of the {self.profile['state_name']} file")
                continue
            self._note_unwritten(w2)
            self.rw(w2)
            self.ro(w2)
            if "RS" in self.employee_records:
                self.rs(w2)
        rw_values = [v for rt, v in self.records if rt == "RW"]
        ro_values = [v for rt, v in self.records if rt == "RO"]
        if not rw_values:
            self.issues.error("input", f"no employees to report for {self.profile.get('state_name', 'this file')}; "
                                       "check the Box 15 state column and the column mapping")
        self._totals("RT", "rw_count", rw_values)
        if ro_values:
            self._totals("RU", "ro_count", ro_values)  # required whenever an RO record is written (SSA 3.2.5)
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
