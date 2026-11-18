"""Round-trip checker for quarterly unemployment-insurance (UI) files in EFW2 format.

Like taxfiling.check, this reads the file back with the layout table alone and works
out on its own what each field should hold. It shares only the layout-level helpers
(splitting records, checking each field's padding and justification) with the W-2
checker, and nothing with the writer.

Checks:
  1. Characters, record length and CR/LF, then every field of every record.
  2. Record order: the profile's record list (for example RA, RE, RS..., RF).
  3. RS: state code, reporting period (the quarter's last month and year), one
     account number, which must be the employer's UI account; taxable wages not
     above total wages; income tax fields empty; one RS per SSN.
  4. RF counts the RW records in the file.
  5. With the source: the same employees in the same order, with the same SSN,
     name, total and taxable wages, and the same totals.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from .check import CheckResult, _check_fields, _d, _norm, split_records
from .layout import Layout
from .model import QuarterlyReport
from .privacy import mask_ssn

INCOME_TAX_FIELDS = ("state_code_2", "state_wages", "state_tax", "local_wages", "local_tax")


def _order_ok(types: list[str], records: list[str]) -> str | None:
    """None if `types` is records[0], records[1], ... with the employee record (RS) repeated."""
    pattern = "".join(f"(?:{rt})+" if rt == "RS" else rt for rt in records)
    return None if re.fullmatch(pattern, "".join(types)) else " ".join(records).replace("RS", "RS...")


def check_quarterly_file(data: bytes | str | Path, layout: Layout, profile: dict, year: int, quarter: int,
                         source: QuarterlyReport | None = None) -> CheckResult:
    raw = data if isinstance(data, bytes) else Path(data).read_bytes()
    result = CheckResult()
    issues = result.issues
    lines = split_records(raw, layout, issues)
    if profile.get("final_line_ending") is False and raw.endswith(b"\r\n"):
        issues.warning("file", "file ends with CR/LF after the last record; the state profile expects none")

    parsed: list[tuple[str, dict | None, str]] = []
    for i, line in enumerate(lines, 1):
        where, rt = f"line {i}", line[:2]
        result.record_counts[rt] += 1
        if rt not in layout.records:
            issues.error(where, f"unknown record type '{rt}'")
            parsed.append((rt, None, where))
            continue
        values = _check_fields(rt, line, layout, where, issues) if len(line) == layout.record_length else None
        for key in profile.get("required_fields", {}).get(rt, []):
            if values is not None and values.get(key) in (None, ""):
                issues.error(where, f"{rt} {layout[rt][key].name} is blank; {profile['state_name']} requires it")
        parsed.append((rt, values, where))

    records = profile.get("records", ["RA", "RE", "RS", "RF"])
    expected_order = _order_ok([rt for rt, _, _ in parsed], records)
    if expected_order:
        issues.error("file", f"records are not in the order {expected_order}")

    n_rw = result.record_counts["RW"]
    for rt, values, where in parsed:
        if rt == "RF" and values is not None and int(values["rw_count"] or 0) != n_rw:
            issues.error(where, f"RF says {values['rw_count']} RW records, file has {n_rw}")
        if rt == "RE" and values is not None and values["tax_year"] != str(year):
            issues.error(where, f"RE tax year {values['tax_year']} is not {year}")

    # ---- RS records ---------------------------------------------------------
    period = f"{quarter * 3:02d}{year}"
    rs = [(v, w) for rt, v, w in parsed if rt == "RS" and v is not None]
    accounts = {v["state_employer_account"] for v, _ in rs}
    if len(accounts) > 1:
        issues.error("file", f"RS records carry {len(accounts)} different state employer account numbers")
    seen: dict[str, str] = {}
    total = taxable = Decimal(0)
    for v, where in rs:
        for key, want in profile.get("state_record_values", {}).items():
            if v[key] != want:
                issues.error(where, f"RS {layout['RS'][key].name} is '{v[key]}', must be '{want}'")
        if v["reporting_period"] != period:
            issues.error(where, f"RS reporting period is '{v['reporting_period']}', must be {period} "
                                f"for {year} Q{quarter}")
        if _d(v["ui_taxable_wages"]) > _d(v["ui_total_wages"]):
            issues.error(where, "RS taxable UI wages are more than total UI wages")
        for key in INCOME_TAX_FIELDS:
            val = v[key]
            if (isinstance(val, str) and val.strip("0 ")) or (isinstance(val, Decimal) and val):
                issues.error(where, f"RS {layout['RS'][key].name} holds income tax data in a UI file")
        if v["ssn"] in seen:
            issues.error(where, f"second RS record for SSN {mask_ssn(v['ssn'])} (first on {seen[v['ssn']]})")
        seen[v["ssn"]] = where
        total += _d(v["ui_total_wages"])
        taxable += _d(v["ui_taxable_wages"])
    if not rs:
        issues.error("file", "file has no employee (RS) records")
    result.totals["ui_total_wages"] = total
    result.totals["ui_taxable_wages"] = taxable

    # ---- compare with the source -----------------------------------------------
    if source is not None:
        state = profile["state"]
        account = re.sub(r"[\s\-]", "", source.employer.ui_accounts.get(state, "")).upper()
        if accounts and accounts != {account}:
            issues.error("file", "RS state employer account does not match the employer's UI account")
        res = [v for rt, v, _ in parsed if rt == "RE" and v is not None]
        if res and res[0]["employer_ein"] != source.employer.ein:
            issues.error("file", "RE EIN does not match the source employer EIN")
        if len(rs) != len(source.employees):
            issues.error("file", f"file has {len(rs)} employees, source has {len(source.employees)} for "
                                 f"{year} Q{quarter}")
        for (v, where), w in zip(rs, source.employees):
            result.employees_compared += 1
            if v["ssn"] != w.ssn:
                issues.error(where, f"SSN {mask_ssn(v['ssn'])} is not the source's {mask_ssn(w.ssn)} ({w.where()})")
            for key, want in (("first_name", w.first_name), ("last_name", w.last_name)):
                result.fields_compared += 1
                if _norm(v[key]) != _norm(want):
                    issues.error(where, f"RS {layout['RS'][key].name} '{v[key]}' does not match the source "
                                        f"'{want}'")
            for key, want in (("ui_total_wages", w.gross_wages), ("ui_taxable_wages", w.taxable_wages)):
                result.fields_compared += 1
                if _d(v[key]) != want:
                    issues.error(where, f"RS {layout['RS'][key].name}: file has {_d(v[key])}, source has {want}")
        src_total = sum((w.gross_wages for w in source.employees), Decimal(0))
        src_taxable = sum((w.taxable_wages for w in source.employees), Decimal(0))
        if (total, taxable) != (src_total, src_taxable):
            issues.error("file", f"file totals {total} / {taxable} differ from the source {src_total} / {src_taxable}")
    return result
