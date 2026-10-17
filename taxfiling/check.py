"""Round-trip checker: read a written file back and prove it says what the input says.

The checker does not reuse the writer's field mapping. It parses the file with
the layout table alone, then works out on its own what each field should hold
from the source data (for example, RW field "code_DD" must equal the Box 12 DD
amount, and RT "total_box1_wages" must equal the sum of RW "box1_wages"). A
mistake in the writer therefore shows up as a mismatch here instead of being
repeated.

Checks, in order:
  1. Characters and record length (ASCII, 512 bytes, CR/LF between records).
  2. Record order (RA, RE, RW [RO] [RS...], RT [RU] [RV], ..., RF).
  3. Every field: constants, blanks, numeric fields, justification. Each record
     is re-encoded from its parsed values and must match byte for byte.
  4. Totals: RT counts and sums against the RW records, RF count.
  5. State rules from the state profile (RS state codes, account number format,
     one RS per employee).
  6. If the source data is given: every employee, name, SSN and amount in the
     file matches the source, and anything in the source that the file does not
     carry is listed.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .issues import Issues
from .layout import Layout, LayoutError, decode_record, encode_record
from .model import W2, WageReport
from .privacy import mask_ssn

NEXT = {
    None: {"RA"},
    "RA": {"RE"},
    "RE": {"RW"},
    "RW": {"RW", "RO", "RS", "RT"},
    "RO": {"RW", "RS", "RT"},
    "RS": {"RW", "RS", "RT"},
    "RT": {"RU", "RV", "RE", "RF"},
    "RU": {"RV", "RE", "RF"},
    "RV": {"RE", "RF"},
    "RF": set(),
}
OPTIONAL_UNCHECKED = {"RO", "RU", "RV"}


@dataclass
class CheckResult:
    issues: Issues = field(default_factory=Issues)
    record_counts: Counter = field(default_factory=Counter)
    totals: dict[str, Decimal] = field(default_factory=dict)
    fields_compared: int = 0
    employees_compared: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues.errors


def split_records(data: bytes, layout: Layout, issues: Issues) -> list[str]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        issues.error("file", f"byte {exc.start} is not ASCII (value {data[exc.start]})")
        text = data.decode("ascii", errors="replace")
    n = layout.record_length
    if "\r\n" in text or len(text) <= n:
        if text.endswith("\r\n"):
            text = text[:-2]
        records = text.split("\r\n")
    else:
        if len(text) % n:
            issues.error("file", f"file has no CR/LF delimiters and its length {len(text)} is not a multiple of {n}")
        records = [text[i:i + n] for i in range(0, len(text), n)]
    for i, rec in enumerate(records, 1):
        if "\r" in rec or "\n" in rec:
            issues.error(f"line {i}", "stray CR or LF inside a record (records must be separated by CR/LF)")
        if rec == "":
            issues.error(f"line {i}", "empty line")
        elif len(rec) != n:
            issues.error(f"line {i}", f"record is {len(rec)} characters, must be {n}")
    return records


def _check_fields(rt: str, line: str, layout: Layout, where: str, issues: Issues) -> dict[str, Any] | None:
    rec = layout[rt]
    try:
        values = decode_record(rec, line)
    except LayoutError as exc:
        issues.error(where, f"{rt} {exc}")
        return None
    for f in rec.fields:
        raw = line[f.slice]
        label = f"{rt} {f.name} (positions {f.start}-{f.end})"
        if f.type == "const" and raw != (f.value or "").ljust(f.length):
            issues.error(where, f"{label} must be '{f.value}'")
        elif f.type == "blank" and raw.strip():
            issues.error(where, f"{label} must be blank")
        elif f.type == "A":
            if raw.startswith(" ") and raw.strip():
                issues.error(where, f"{label} is not left-justified")
            if f.case != "mixed" and raw != raw.upper():
                issues.warning(where, f"{label} has lowercase letters")
            if any(ord(c) < 32 or ord(c) > 126 for c in raw):
                issues.error(where, f"{label} has non-printable characters")
        if f.required and f.type in ("A", "N") and not raw.strip():
            issues.error(where, f"{label} is required but blank")
    try:
        again = encode_record(rec, {k: v for k, v in values.items() if k != "record_id"}, layout.record_length)
    except LayoutError as exc:
        issues.error(where, f"{rt} does not re-encode: {exc}")
        return values
    if again != line and again.upper() != line.upper():  # case alone is reported above
        bad = next(f for f in rec.fields if again[f.slice] != line[f.slice])
        issues.error(where, f"{rt} {bad.name} (positions {bad.start}-{bad.end}) is not padded or justified "
                            f"as the layout requires")
    return values


def _d(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal(0)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").upper()).strip()


def _expected_rw(w2: W2, key: str) -> Decimal:
    """What an RW money field should hold, worked out from the field key itself.
    (The two Box 11 fields are compared as a pair, since the source has one Box 11 amount.)"""
    if key.startswith("code_"):
        return w2.box12.get(key[5:], Decimal(0))
    return getattr(w2, key)


def _fits(written: str, source: str, width: int) -> bool:
    """True if `written` is `source`, or `source` cut to the field width (both normalized)."""
    return written == source or (len(source) > width and written == source[:width].rstrip())


def _address_matches(delivery: str, location: str, src_delivery: str, src_location: str,
                     delivery_width: int, location_width: int) -> str | None:
    """Compare a written (delivery, location) pair with the source street address.

    Returns "exact"; "shortened" when each written part is the matching source part, or that
    part cut to its field width (as when a street is too long, or a unit number was moved to
    the location field); or None.
    """
    src = _norm(f"{src_delivery} {src_location}")
    if _norm(f"{delivery} {location}") == src:
        return "exact"
    if not delivery:
        return None
    if src_location:
        splits = [(_norm(src_delivery), _norm(src_location))]
    else:  # the writer may have moved the end of one source line (a unit) to the location field
        words = src.split(" ")
        splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words) + 1)]
    for d, loc in splits:
        if _fits(delivery, d, delivery_width) and _fits(location, loc, location_width):
            return "shortened"
    return None


def _compare_text(label: str, written: str, source: str, where: str, issues: Issues,
                  width: int | None = None) -> None:
    """Exact match, or (when a width is given) the source cut to that width, which is a warning."""
    w, s = _norm(written), _norm(source)
    if w == s:
        return
    if width and _fits(w, s, width):
        issues.warning(where, f"{label} shortened to fit: '{s}' written as '{w}'")
        return
    issues.error(where, f"{label} is '{w}' in the file but '{s}' in the source")


def _compare_address(rec: dict, w2: W2, rt: str, layout: Layout, where: str, issues: Issues) -> None:
    delivery, location = _norm(rec["delivery_address"]), _norm(rec["location_address"])
    src = _norm(f"{w2.address.delivery} {w2.address.location}")
    match = _address_matches(delivery, location, w2.address.delivery, w2.address.location,
                             layout[rt]["delivery_address"].length, layout[rt]["location_address"].length)
    if match is None:
        issues.error(where, f"{rt} street address '{_norm(f'{delivery} {location}')}' does not match "
                            f"source '{src}'")
    elif match == "shortened":
        issues.warning(where, f"{rt} street address shortened to fit: '{src}' written as "
                              f"'{delivery}' + '{location}'")
    _compare_text(f"{rt} city", rec["city"], w2.address.city, where, issues, width=layout[rt]["city"].length)
    for key, src_val in (("state", w2.address.state), ("zip", w2.address.zip), ("zip_ext", w2.address.zip_ext)):
        _compare_text(f"{rt} {key}", rec[key], src_val, where, issues)


def _compare_employee(rw: dict, rs_list: list[dict], w2: W2, layout: Layout, profile: dict,
                      result: CheckResult) -> None:
    issues, where = result.issues, w2.where()
    if rw["ssn"] != w2.ssn:
        issues.error(where, f"RW SSN {mask_ssn(rw['ssn'] or '')} does not match source {mask_ssn(w2.ssn)}")
        return
    result.employees_compared += 1
    for key in ("first_name", "middle_name", "last_name", "suffix"):
        _compare_text(f"RW {key}", rw[key], getattr(w2, key), where, issues)
        result.fields_compared += 1
    _compare_address(rw, w2, "RW", layout, where, issues)
    for f in layout["RW"].fields:
        if f.type != "M" or f.key.startswith("box11_"):
            continue
        expected, got = _expected_rw(w2, f.key), _d(rw[f.key])
        result.fields_compared += 1
        if got != expected:
            issues.error(where, f"RW {f.name}: file has {got}, source has {expected}")
    box11 = sum((_d(rw[f.key]) for f in layout["RW"].fields if f.key.startswith("box11_")), Decimal(0))
    result.fields_compared += 1
    if box11 != w2.box11_nonqualified:
        issues.error(where, f"RW Box 11 nonqualified plans (457 + non-457): file has {box11}, "
                            f"source has {w2.box11_nonqualified}")
    for key in ("statutory_employee", "retirement_plan", "third_party_sick_pay"):
        result.fields_compared += 1
        if rw[key] != ("1" if getattr(w2, key) else "0"):
            issues.error(where, f"RW {key} indicator is {rw[key]}, source says {getattr(w2, key)}")

    rw_codes = {f.key[5:] for f in layout["RW"].fields if f.key.startswith("code_")}
    for code, amount in sorted(w2.box12.items()):
        if code not in rw_codes and amount:
            issues.warning(where, f"Box 12 {code} {amount} is in the source but not in this file "
                                  "(it belongs in the RO record)")
    if w2.box8_allocated_tips:
        issues.warning(where, f"Box 8 {w2.box8_allocated_tips} is in the source but not in this file")

    state = profile.get("state")
    if not state:
        return
    lines = [s for s in w2.states if s.state == state]
    mine = [r for r in rs_list if r.get("state_code") == profile["fips"]]
    if not lines:
        if mine:
            issues.error(where, f"file has an RS record for {state} but the source has no {state} wages")
        return
    if len(mine) != 1:
        if not profile.get("one_state_record_per_employee"):  # otherwise already reported above
            issues.error(where, f"expected one {state} RS record, found {len(mine)}")
        return
    rs, st = mine[0], lines[0]
    for key in ("first_name", "middle_name", "last_name", "suffix"):
        _compare_text(f"RS {key}", rs[key], getattr(w2, key), where, issues)
    _compare_address(rs, w2, "RS", layout, where, issues)
    for label, got, expected in (("state wages (Box 16)", rs["state_wages"], st.wages),
                                 ("state tax (Box 17)", rs["state_tax"], st.tax)):
        result.fields_compared += 1
        if got != expected:
            issues.error(where, f"RS {label}: file has {got}, source has {expected}")
    account = re.sub(r"[\s\-]", "", st.employer_state_id).upper()
    result.fields_compared += 1
    if rs["state_employer_account"] != account:
        issues.error(where, f"RS state account '{rs['state_employer_account']}' does not match Box 15 '{account}'")


def check_file(data: bytes | str | Path, layout: Layout, profile: dict | None = None,
               source: WageReport | None = None) -> CheckResult:
    profile = profile or {}
    raw = data if isinstance(data, bytes) else Path(data).read_bytes()
    result = CheckResult()
    issues = result.issues
    lines = split_records(raw, layout, issues)

    if profile.get("final_line_ending") is False and raw.endswith(b"\r\n"):
        issues.warning("file", "file ends with CR/LF after the last record; the state profile expects none")

    parsed: list[tuple[str, dict | None, str]] = []
    prev = None
    for i, line in enumerate(lines, 1):
        where = f"line {i}"
        rt = line[:2]
        result.record_counts[rt] += 1
        if rt not in NEXT:
            issues.error(where, f"unknown record type '{rt}'")
            continue
        if rt not in NEXT.get(prev, set()):
            expected = " or ".join(sorted(NEXT.get(prev, set()))) or "end of file"
            issues.error(where, f"{rt} record cannot follow {prev or 'start of file'}; expected {expected}")
        prev = rt
        if len(line) != layout.record_length:
            parsed.append((rt, None, where))
            continue
        if rt in OPTIONAL_UNCHECKED or rt not in layout.records:
            issues.warning(where, f"{rt} record present; its layout is not loaded, so its fields are not checked")
            parsed.append((rt, None, where))
            continue
        parsed.append((rt, _check_fields(rt, line, layout, where, issues), where))
    if prev != "RF":
        issues.error("file", "last record must be RF")
    if result.record_counts["RW"] == 0:
        issues.error("file", "file has no employee (RW) records")
    if result.record_counts["RA"] != 1 or result.record_counts["RF"] != 1:
        issues.error("file", "file must have exactly one RA and one RF record")

    # ---- totals, per employer block --------------------------------------
    all_rw = 0
    blocks: list[dict] = []
    block: dict | None = None
    for rt, values, where in parsed:
        if rt == "RE":
            block = {"re": values, "where": where, "employees": [], "rt": None}
            blocks.append(block)
        elif block is None:
            continue
        elif rt == "RW":
            block["employees"].append({"rw": values, "rs": [], "where": where})
            all_rw += 1
        elif rt == "RS" and block["employees"]:
            block["employees"][-1]["rs"].append(values)
        elif rt == "RT":
            block["rt"] = (values, where)
        elif rt == "RF" and values is not None:
            if values["rw_count"] is None or int(values["rw_count"]) != all_rw:
                issues.error(where, f"RF says {values['rw_count']} RW records, file has {all_rw}")

    for b in blocks:
        rws = [e["rw"] for e in b["employees"] if e["rw"] is not None]
        if b["rt"] is None:
            issues.error(b["where"], "employer block has no RT record")
            continue
        rt_values, where = b["rt"]
        if rt_values is None:
            continue
        if int(rt_values["rw_count"] or 0) != len(b["employees"]):
            issues.error(where, f"RT says {int(rt_values['rw_count'] or 0)} RW records, block has "
                                f"{len(b['employees'])}")
        for f in layout["RT"].fields:
            if f.type != "M":
                continue
            computed = sum((_d(r[f.sum_of]) for r in rws), Decimal(0)) if f.sum_of else Decimal(0)
            written = _d(rt_values[f.key])
            result.totals[f.key] = result.totals.get(f.key, Decimal(0)) + written
            if written != computed:
                issues.error(where, f"RT {f.name} is {written}, the RW records add up to {computed}")

        # ---- state rules -------------------------------------------------
        if profile.get("state_record"):
            fips = profile["fips"]
            rule = profile.get("account_number")
            if b["re"] is not None and source is not None and b["re"]["tax_year"] != str(source.tax_year):
                issues.error(b["where"], f"RE tax year {b['re']['tax_year']} is not {source.tax_year}")
            one_each = profile.get("one_state_record_per_employee", False)
            for e in b["employees"]:
                mine = [r for r in e["rs"] if r is not None and r["state_code"] == fips]
                if not mine:
                    (issues.error if one_each else issues.warning)(
                        e["where"], f"RW has no RS record for {profile['state_name']} (state code {fips})")
                elif len(mine) > 1 and one_each:
                    issues.error(e["where"], f"RW has {len(mine)} RS records for {profile['state_name']}; "
                                             "one per employee is required")
                for r in e["rs"]:
                    if r is None:
                        continue
                    if e["rw"] is not None and r["ssn"] != e["rw"]["ssn"]:
                        issues.error(e["where"], "RS SSN differs from the SSN on the RW record before it")
                    for key, want in profile.get("state_record_values", {}).items():
                        if r[key] != want:
                            issues.error(e["where"], f"RS {layout['RS'][key].name} is '{r[key]}', must be '{want}'")
                    if rule and not re.fullmatch(rule["pattern"], r["state_employer_account"]):
                        issues.error(e["where"], f"RS state account '{r['state_employer_account']}' does not "
                                                 f"match: {rule['description']}")
                    result.totals["state_wages"] = result.totals.get("state_wages", Decimal(0)) + _d(r["state_wages"])
                    result.totals["state_tax"] = result.totals.get("state_tax", Decimal(0)) + _d(r["state_tax"])

    # ---- compare with the source -----------------------------------------
    if source is not None:
        employees = [e for b in blocks for e in b["employees"]]
        expected = source.employees
        if profile.get("only_employees_with_state_wages"):
            expected = [w for w in source.employees if any(s.state == profile["state"] for s in w.states)]
            for w in source.employees:
                if w not in expected:
                    issues.warning(w.where(), f"not in the file: no {profile['state_name']} wages in the source")
        left_out = len(source.employees) - len(expected)
        if len(blocks) != 1:
            issues.error("file", f"file has {len(blocks)} employer blocks; the source has one employer")
        elif blocks[0]["re"] is not None:
            re_vals = blocks[0]["re"]
            if re_vals["employer_ein"] != source.employer.ein:
                issues.error(blocks[0]["where"], "RE EIN does not match the source employer EIN")
            _compare_text("RE employer name", re_vals["employer_name"], source.employer.name,
                          blocks[0]["where"], issues)
        if len(employees) != len(expected):
            extra = f" ({left_out} without {profile.get('state_name')} wages left out)" if left_out else ""
            issues.error("file", f"file has {len(employees)} employees, source has {len(expected)}{extra}")
        for e, w2 in zip(employees, expected):
            if e["rw"] is None:
                continue
            _compare_employee(e["rw"], [r for r in e["rs"] if r is not None], w2, layout, profile, result)
    return result


def format_report(result: CheckResult, title: str = "Check") -> str:
    out = [f"{title}: {'PASSED' if result.ok else 'FAILED'}"]
    counts = ", ".join(f"{rt} {n}" for rt, n in result.record_counts.items())
    out.append(f"  Records: {counts}")
    if result.employees_compared:
        out.append(f"  Compared with source: {result.employees_compared} employees, "
                   f"{result.fields_compared} fields")
    labels = [("total_box1_wages", "Box 1 wages"), ("total_box2_fed_tax", "Box 2 federal tax"),
              ("total_box3_ss_wages", "Box 3 SS wages"), ("total_box5_medicare_wages", "Box 5 Medicare wages"),
              ("state_wages", "State wages (RS)"), ("state_tax", "State tax (RS)")]
    for key, label in labels:
        if key in result.totals:
            out.append(f"  {label:<22}{result.totals[key]:>16,.2f}")
    errors, warnings = result.issues.errors, result.issues.warnings
    out.append(f"  {len(errors)} error(s), {len(warnings)} warning(s)")
    for issue in errors + warnings:
        out.append(f"    {issue}")
    return "\n".join(out)
