"""Check a proposed mapping and turn it into the mapping file the Excel reader uses.

The model's answer is untrusted input. Nothing in it is used until it passes these
checks, which run locally and do not depend on which provider answered:

* the sheet and every column must exist in the profiled workbook, in that sheet;
* every field must be a known W-2 field, mapped once; every column used once;
* every required field must be mapped (or the mapping is marked incomplete);
* each mapped column's local profile must fit the field (an SSN column should look
  like ###-##-####, a money column should hold numbers, a flag column Y/N, and so on).
  A mismatch is a warning shown next to the mapping for the person reviewing it.

The written mapping file names the provider, the model and the payload hash, so the
audit log entry that produced it can be found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fields import BY_KEY, FIELDS, OPTIONAL, REQUIRED
from .profile import ColumnProfile, WorkbookProfile

CONFIDENCE = {"high", "medium", "low"}


@dataclass
class Review:
    sheet: str = ""
    header_row: int | None = None
    rows: list[dict] = field(default_factory=list)      # field, column, header, shapes, confidence, reason, check
    box12: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    unmapped_optional: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors and not self.missing_required


def _fits(kind: str, col: ColumnProfile) -> str | None:
    """Does the column's profile fit what the field should hold? A reason if not."""
    types, shapes, vocab = col.types, set(col.shapes), set(col.vocabularies)
    text_only = set(types) <= {"text"}
    numeric = set(types) <= {"integer", "decimal"}
    if not col.filled:
        return "the column is empty" if kind in ("ssn", "ein", "name", "money") else None
    if kind == "ssn" and not (shapes <= {"###-##-####", "#########", "#"} and (text_only or numeric)):
        return f"values do not look like SSNs (shapes {sorted(shapes)})"
    if kind == "ein" and not (shapes <= {"##-#######", "#########", "#"}):
        return f"values do not look like EINs (shapes {sorted(shapes)})"
    if kind == "zip" and not all(re.fullmatch(r"#{5}(-?#{4})?|#", s) for s in shapes):
        return f"values do not look like ZIP codes (shapes {sorted(shapes)})"
    if kind == "state" and "us_state_code" not in vocab:
        return "not every value is a U.S. state code"
    if kind == "flag" and not (vocab & {"yes_no_flag"} or types.keys() <= {"boolean"} or
                               (numeric and shapes <= {"#"})):
        return "values are not all Y/N flags"
    if kind == "money" and not (numeric or all(re.fullmatch(r"\$?[#,]+(\.#+)?", s) for s in shapes)):
        return f"values are not all numbers (types {types})"
    if kind == "name" and any(ch == "#" for s in shapes for ch in s):
        return "some values contain digits"
    return None


def check_proposal(answer: dict, profile: WorkbookProfile) -> tuple[dict | None, Review]:
    review = Review()
    if not isinstance(answer, dict):
        review.errors.append("the answer is not a JSON object")
        return None, review
    sheet = profile.sheet(str(answer.get("sheet", "")))
    if sheet is None:
        review.errors.append(f"the answer names sheet {answer.get('sheet')!r}, which is not in the workbook")
        return None, review
    review.sheet, review.header_row = sheet.name, sheet.header_row
    review.notes = [str(n)[:300] for n in answer.get("notes", []) if isinstance(n, str)]

    def column(cid) -> ColumnProfile | None:
        found = profile.column(str(cid))
        if found is None:
            review.errors.append(f"the answer names column {cid!r}, which is not in the workbook")
            return None
        sp, col = found
        if sp is not sheet:
            review.errors.append(f"column {cid} is on sheet {sp.name!r}, not the chosen sheet {sheet.name!r}")
            return None
        if not col.header:
            review.errors.append(f"column {cid} ({col.letter}) has no header, so the reader cannot find it")
            return None
        return col

    used: dict[str, str] = {}
    columns: dict[str, str] = {}

    def use(col: ColumnProfile, purpose: str) -> bool:
        if col.id in used:
            review.errors.append(f"column {col.id} ({col.header!r}) is used for both {used[col.id]} and {purpose}")
            return False
        if list(c.header for c in sheet.columns).count(col.header) > 1:
            review.errors.append(f"header {col.header!r} appears more than once in the sheet; rename one")
            return False
        used[col.id] = purpose
        return True

    for item in answer.get("columns", []):
        if not isinstance(item, dict):
            review.errors.append("a column entry is not an object")
            continue
        key = item.get("field")
        if key not in BY_KEY:
            review.errors.append(f"unknown field {key!r}")
            continue
        if key in columns:
            review.errors.append(f"field {key} is mapped twice")
            continue
        col = column(item.get("column"))
        if col is None or not use(col, key):
            continue
        columns[key] = col.header
        confidence = item.get("confidence") if item.get("confidence") in CONFIDENCE else "low"
        check = _fits(BY_KEY[key].kind, col)
        if check:
            review.warnings.append(f"{key} <- {col.letter} {col.header!r}: {check}")
        review.rows.append({"field": key, "column": col.letter, "header": col.header, "shapes": col.shapes,
                            "confidence": confidence, "reason": str(item.get("reason", ""))[:200],
                            "check": check or "ok"})

    box12 = []
    for pair in answer.get("box12", []):
        if not isinstance(pair, dict):
            continue
        code, amount = column(pair.get("code_column")), column(pair.get("amount_column"))
        if code is None or amount is None or not use(code, "Box 12 code") or not use(amount, "Box 12 amount"):
            continue
        if code.filled and "box12_code" not in code.vocabularies:
            review.warnings.append(f"Box 12 code column {code.letter} {code.header!r}: not every value is a Box 12 code")
        amount_check = _fits("money", amount)
        if amount_check:
            review.warnings.append(f"Box 12 amount column {amount.letter} {amount.header!r}: {amount_check}")
        box12.append({"code": code.header, "amount": amount.header})
        review.box12.append({"code": f"{code.letter} {code.header}", "amount": f"{amount.letter} {amount.header}"})

    order = [f.key for f in FIELDS]
    review.rows.sort(key=lambda r: order.index(r["field"]))
    review.missing_required = [k for k in REQUIRED if k not in columns]
    review.unmapped_optional = [k for k in OPTIONAL if k not in columns]
    if review.errors:
        return None, review
    mapping = {
        "sheet": sheet.name,
        "header_row": sheet.header_row,
        "optional": [k for k in OPTIONAL if k in columns],
        "columns": {k: columns[k] for k in order if k in columns},
        "box12": box12,
    }
    return mapping, review


def format_review(review: Review, provider: str) -> str:
    lines = [f"Proposed mapping ({provider}) for sheet {review.sheet!r}, headers on row {review.header_row}:"]
    for r in review.rows:
        flag = "" if r["check"] == "ok" else "   << CHECK: " + r["check"]
        lines.append(f"  {r['field']:<26} <- {r['column']:>3}  {r['header'][:38]:<38} "
                     f"{', '.join(r['shapes'][:2])[:22]:<22} {r['confidence']:<6}{flag}")
    for b in review.box12:
        lines.append(f"  {'box12 pair':<26} <- {b['code']}  +  {b['amount']}")
    if review.missing_required:
        lines.append(f"Required fields not mapped: {', '.join(review.missing_required)}")
    for n in review.notes:
        lines.append(f"  note: {n}")
    for w in review.warnings:
        lines.append(f"  WARNING: {w}")
    for e in review.errors:
        lines.append(f"  ERROR: {e}")
    return "\n".join(lines)
