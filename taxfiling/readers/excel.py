"""Read W-2 rows from an Excel workbook using a column mapping.

The mapping (see taxfiling/mappings/w2_standard.json) says which spreadsheet
header holds which W-2 field. It is the only thing the planned AI step will
produce, and it is produced from headers alone; this module applies it to the
full rows on the local machine.

Excel quirks handled here, each reported as a warning rather than silently fixed:
  * SSNs or ZIP codes stored as numbers lose their leading zeros.
  * Amounts with more than two decimal places are rounded to cents.
"""
from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..issues import Issues
from ..model import Address, Employer, StateWages, W2

MAPPING_DIR = Path(__file__).resolve().parent.parent / "mappings"

MONEY_FIELDS = [
    "box1_wages", "box2_fed_tax", "box3_ss_wages", "box4_ss_tax", "box5_medicare_wages",
    "box6_medicare_tax", "box7_ss_tips", "box8_allocated_tips", "box10_dependent_care",
    "box11_nonqualified",
]
FLAG_FIELDS = ["statutory_employee", "retirement_plan", "third_party_sick_pay"]


def load_mapping(name_or_path: str | Path) -> dict:
    path = Path(name_or_path)
    if not path.suffix:
        path = MAPPING_DIR / f"{name_or_path}.json"
    return json.loads(path.read_text())


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _digits(value: Any, width: int, label: str, where: str, issues: Issues, *, secret: bool = False) -> str:
    """Strip punctuation from an ID; restore leading zeros Excel dropped from a number cell.

    secret=True keeps the number itself out of the warning (used for SSNs)."""
    if value is None or _text(value) == "":
        return ""
    if isinstance(value, (int, float)):
        text = _text(value)
        if text.isdigit() and len(text) < width:
            shown = f"ending {text[-4:]}" if secret else text
            fixed = "" if secret else f" to {text.zfill(width)}"
            issues.warning(where, f"{label} {shown} was stored as a number, which drops leading zeros; "
                                  f"zeros restored{fixed}. Check it against the source record.")
            return text.zfill(width)
        return text
    return re.sub(r"[\s\-]", "", str(value))


def _money(value: Any, label: str, where: str, issues: Issues) -> Decimal:
    if value is None or _text(value) == "":
        return Decimal(0)
    try:
        d = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        issues.error(where, f"{label}: '{value}' is not a number")
        return Decimal(0)
    cents = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cents != d:
        issues.warning(where, f"{label}: {value} has more than 2 decimal places; rounded to {cents}")
    return cents


def _flag(value: Any, label: str, where: str, issues: Issues) -> bool:
    text = _text(value).upper()
    if text in ("Y", "YES", "TRUE", "X", "1"):
        return True
    if text in ("", "N", "NO", "FALSE", "0"):
        return False
    issues.error(where, f"{label}: '{value}' is not Y or N")
    return False


def read_w2_workbook(path: str | Path, mapping: dict) -> tuple[Employer, list[W2], Issues]:
    issues = Issues()
    wb = load_workbook(path, data_only=True, read_only=True)
    sheet = mapping.get("sheet")
    if sheet not in wb.sheetnames:
        raise ValueError(f"{path}: no sheet named '{sheet}' (found {wb.sheetnames})")
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    header_row = mapping.get("header_row", 1)
    for _ in range(header_row - 1):
        next(rows)
    headers = [_text(h) for h in next(rows)]
    index = {h: i for i, h in enumerate(headers) if h}

    cols = mapping["columns"]
    missing = sorted({h for h in cols.values() if h not in index}
                     | {h for pair in mapping.get("box12", []) for h in pair.values() if h not in index})
    if missing:
        raise ValueError(f"{path} [{sheet}]: mapped column(s) not found: {missing}")

    employers: dict[str, Employer] = {}
    employees: list[W2] = []
    for rownum, row in enumerate(rows, start=header_row + 1):
        def get(field: str) -> Any:
            header = cols.get(field)
            if header is None:
                return None
            i = index[header]
            return row[i] if i < len(row) else None

        first, last = _text(get("first_name")), _text(get("last_name"))
        if not first and not last and _text(get("ssn")) == "":
            continue  # blank line or a totals row such as "TOTAL"
        where = f"row {rownum}"

        ein = _digits(get("employer_ein"), 9, "Employer EIN", where, issues)
        if ein and ein not in employers:
            employers[ein] = Employer(
                ein=ein,
                name=_text(get("employer_name")),
                address=Address(
                    delivery=_text(get("employer_address")), city=_text(get("employer_city")),
                    state=_text(get("employer_state")).upper(),
                    zip=_digits(get("employer_zip"), 5, "Employer ZIP", where, issues),
                ),
            )
        employer = employers.get(ein)

        zip_full = _digits(get("zip"), 5, "ZIP code", where, issues)
        w2 = W2(
            control_number=_text(get("control_number")),
            ssn=_digits(get("ssn"), 9, "SSN", where, issues, secret=True),
            first_name=first,
            middle_name=_text(get("middle_name")),
            last_name=last,
            suffix=_text(get("suffix")),
            address=Address(
                delivery=_text(get("address")), location=_text(get("address_line_2")),
                city=_text(get("city")), state=_text(get("state")).upper(),
                zip=zip_full[:5], zip_ext=zip_full[5:9],
            ),
            box14b_tipped_occupation=_text(get("box14b_tipped_occupation")),
            source_row=rownum,
        )
        for name in MONEY_FIELDS:
            setattr(w2, name, _money(get(name), name, where, issues))
        for name in FLAG_FIELDS:
            setattr(w2, name, _flag(get(name), name, where, issues))
        for pair in mapping.get("box12", []):
            code = _text(row[index[pair["code"]]]).upper()
            amount = _money(row[index[pair["amount"]]], f"Box 12 {code}", where, issues)
            if not code:
                if amount:
                    issues.error(where, f"Box 12 amount {amount} has no code")
                continue
            if code in w2.box12:
                issues.error(where, f"Box 12 code {code} appears twice")
            w2.box12[code] = amount
        state = _text(get("box15_state")).upper()
        if state:
            state_id = _text(get("box15_state_id"))
            w2.states.append(StateWages(
                state=state, employer_state_id=state_id,
                wages=_money(get("box16_state_wages"), "Box 16", where, issues),
                tax=_money(get("box17_state_tax"), "Box 17", where, issues),
            ))
            if employer is not None and state_id:
                known = employer.state_ids.setdefault(state, state_id)
                if known != state_id:
                    issues.error(where, f"Box 15 {state} ID {state_id} differs from {known} on earlier rows")
        employees.append(w2)

    wb.close()
    if not employers:
        raise ValueError(f"{path}: no employer EIN found in column '{cols.get('employer_ein')}'")
    if len(employers) > 1:
        issues.error("workbook", f"rows belong to {len(employers)} employers ({', '.join(employers)}); "
                                 "one file per employer is supported so far")
    return next(iter(employers.values())), employees, issues
