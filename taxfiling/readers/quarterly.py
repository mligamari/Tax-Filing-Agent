"""Read one quarter of unemployment-insurance wages from an Excel workbook.

Same approach as the W-2 reader: a column mapping (taxfiling/mappings/quarterly_standard.json)
names the header for each field, and the full rows are read only on the local machine.
The sheet may hold every quarter of the year; only rows for the requested year and
quarter are kept.

Checks made here, so a bad number never reaches a file:
  * gross wages = excess wages + taxable wages, and none are negative;
  * one row per SSN in the quarter;
  * the year and quarter columns hold whole numbers, quarter 1 to 4.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..issues import Issues
from ..model import QuarterlyWages
from .excel import _digits, _flag, _money, _text, load_mapping  # noqa: F401  (load_mapping re-exported)


def _whole(value: Any) -> int | None:
    text = _text(value)
    return int(text) if text.isdigit() else None


def read_quarterly_workbook(path: str | Path, mapping: dict, tax_year: int,
                            quarter: int) -> tuple[list[QuarterlyWages], Issues]:
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1, 2, 3 or 4, not {quarter}")
    issues = Issues()
    wb = load_workbook(path, data_only=True, read_only=True)
    sheet = mapping.get("sheet")
    if sheet not in wb.sheetnames:
        raise ValueError(f"{path}: no sheet named '{sheet}' (found {wb.sheetnames})")
    rows = wb[sheet].iter_rows(values_only=True)
    header_row = mapping.get("header_row", 1)
    for _ in range(header_row - 1):
        next(rows)
    headers = [_text(h) for h in next(rows)]
    index = {h: i for i, h in enumerate(headers) if h}
    optional = set(mapping.get("optional", []))
    cols = {f: h for f, h in mapping["columns"].items() if h in index or f not in optional}
    missing = sorted(h for h in cols.values() if h not in index)
    if missing:
        raise ValueError(f"{path} [{sheet}]: mapped column(s) not found: {missing}")

    seen: dict[str, int] = {}
    out: list[QuarterlyWages] = []
    for rownum, row in enumerate(rows, start=header_row + 1):
        def get(field: str) -> Any:
            header = cols.get(field)
            if header is None:
                return None
            i = index[header]
            return row[i] if i < len(row) else None

        first, last = _text(get("first_name")), _text(get("last_name"))
        if not first and not last and _text(get("ssn")) == "":
            continue  # blank line or a totals row
        where = f"row {rownum}"
        year, qtr = _whole(get("tax_year")), _whole(get("quarter"))
        if year is None or qtr not in (1, 2, 3, 4):
            issues.error(where, f"tax year '{_text(get('tax_year'))}' / quarter '{_text(get('quarter'))}' "
                                "is not a year and a quarter 1-4")
            continue
        if (year, qtr) != (tax_year, quarter):
            continue

        w = QuarterlyWages(
            control_number=_text(get("control_number")),
            ssn=_digits(get("ssn"), 9, "SSN", where, issues, secret=True),
            first_name=first, middle_name=_text(get("middle_name")), last_name=last,
            gross_wages=_money(get("gross_wages"), "UI gross wages", where, issues),
            excess_wages=_money(get("excess_wages"), "UI excess wages", where, issues),
            taxable_wages=_money(get("taxable_wages"), "UI taxable wages", where, issues),
            employed_12th=tuple(_flag(get(f"employed_12th_m{m}"), f"employed on 12th, month {m}", where, issues)
                                for m in (1, 2, 3)),  # type: ignore[arg-type]
            source_row=rownum,
        )
        for label, amount in (("gross", w.gross_wages), ("excess", w.excess_wages), ("taxable", w.taxable_wages)):
            if amount < 0:
                issues.error(where, f"UI {label} wages {amount} are negative")
        if w.excess_wages + w.taxable_wages != w.gross_wages:
            issues.error(where, f"UI gross wages {w.gross_wages} do not equal excess {w.excess_wages} "
                                f"+ taxable {w.taxable_wages}")
        if w.ssn in seen:
            issues.error(where, f"the same SSN is already on row {seen[w.ssn]} for {tax_year} Q{quarter}; "
                                "a quarterly report has one line per employee")
        elif w.ssn:
            seen[w.ssn] = rownum
        out.append(w)

    wb.close()
    if not out:
        issues.error("workbook", f"no rows for {tax_year} quarter {quarter} in sheet '{sheet}'")
    return out, issues


def load_quarterly_report(path: str | Path, state: str, tax_year: int, quarter: int,
                          mapping: dict | None = None, w2_mapping: dict | None = None):
    """A QuarterlyReport from a workbook that has both a W-2 sheet and a quarterly wages sheet.

    The quarterly sheet gives the wages; the W-2 sheet gives the employer (name, EIN,
    address) and each employee's address and name suffix, matched by SSN. Returns
    (report, issues); the report has no submitter yet."""
    from ..model import QuarterlyReport
    from .excel import read_w2_workbook

    mapping = mapping or load_mapping("quarterly_standard")
    w2_mapping = w2_mapping or load_mapping("w2_standard")
    employer, w2s, issues = read_w2_workbook(path, w2_mapping)
    rows, q_issues = read_quarterly_workbook(path, mapping, tax_year, quarter)
    issues.extend(q_issues)
    by_ssn = {w.ssn: w for w in w2s if w.ssn}
    for r in rows:
        w2 = by_ssn.get(r.ssn)
        if w2 is None:
            issues.warning(r.where(), "no W-2 row with this SSN, so no address for this employee")
            continue
        r.address = w2.address
        r.suffix = r.suffix or w2.suffix
        r.middle_name = r.middle_name or w2.middle_name
        if r.last_name.upper() != w2.last_name.upper() or r.first_name.upper() != w2.first_name.upper():
            issues.error(r.where(), f"name differs from the W-2 row with the same SSN ({w2.where()})")
    report = QuarterlyReport(tax_year=tax_year, quarter=quarter, state=state.upper(), employer=employer,
                             employees=rows)
    return report, issues
