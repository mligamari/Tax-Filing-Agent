"""Describe a workbook's columns without revealing any cell value.

A column profile is what the model sees in place of the data:

* the column's header (withheld if it looks like data, see boundary.header_problem);
* how many cells are filled, and what Python type they are (text, integer, decimal, date);
* up to three *shapes*: the cell's pattern with every letter run collapsed to A/a/Aa
  and every digit replaced by '#', so '237-96-5232' becomes '###-##-####' and
  'Lauren Ramirez' becomes 'Aa Aa' (a name's length is not shown). Amounts are shown by
  format only ('#.##' for a number, '$#,###.##' for text such as '$82,297.12'), so a
  shape never reveals a wage's size;
* vocabulary labels when every filled cell belongs to a public list (U.S. state codes,
  Box 12 codes, yes/no flags). The label is sent; the values are not.

No cell below the header row is copied into a profile, not even partially.
"""
from __future__ import annotations

import datetime as _dt
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from ..formats.efw2 import W2_BOX12
from ..validate import US_STATES

MAX_SHAPES = 3
MAX_SHAPE_LENGTH = 24
HEADER_SCAN_ROWS = 10
YES_NO = {"Y", "N", "YES", "NO", "TRUE", "FALSE", "X"}
VOCABULARIES = {
    "us_state_code": US_STATES,
    "box12_code": W2_BOX12 | {"TT"},
    "yes_no_flag": YES_NO,
}


# Every key, type name, shape word and label a column profile can contain. They are the
# program's own words, so a cell that happens to say "Date" or "Text" is not treated as data.
PROFILE_LABELS = {
    "id", "column", "header", "header_withheld", "filled", "types", "shapes", "all_values_are",
    "name", "name_withheld", "header_row", "columns", "sheets", "fields", "field", "description",
    "required", "workbook", "text", "integer", "decimal", "date", "boolean", "TRUE/FALSE",
    "blank header", *VOCABULARIES,
}


@dataclass
class Grid:
    """Every cell of every sheet, read once. Rows are 1-based in messages, 0-based here."""
    sheets: dict[str, list[tuple[Any, ...]]]


def read_grid(path: str | Path, sheets: list[str] | None = None) -> Grid:
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        names = sheets or wb.sheetnames
        missing = [s for s in names if s not in wb.sheetnames]
        if missing:
            raise ValueError(f"{path}: no sheet named {missing} (found {wb.sheetnames})")
        return Grid({name: [tuple(r) for r in wb[name].iter_rows(values_only=True)] for name in names})
    finally:
        wb.close()


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def detect_header_row(rows: list[tuple[Any, ...]]) -> int | None:
    """Index of the header row: the first of the top rows with at least two filled cells,
    all of them text, whose next row is not also all text (that is, data follows).
    Falls back to the first row with two or more text cells."""
    fallback = None
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        filled = [v for v in row if not _blank(v)]
        if len(filled) < 2 or not all(isinstance(v, str) for v in filled):
            continue
        if fallback is None:
            fallback = i
        below = [v for r in rows[i + 1:i + 3] for v in r if not _blank(v)]
        if below and not all(isinstance(v, str) for v in below):
            return i
    return fallback


def shape(value: Any) -> str:
    """A cell's pattern with no letter, digit or other character of the value left in it,
    except ASCII punctuation."""
    if isinstance(value, bool):
        return "TRUE/FALSE"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "date"
    if isinstance(value, int):
        return "#"
    if isinstance(value, (float, Decimal)):
        text = f"{value}"
        decimals = len(text.split(".")[1]) if "." in text and "e" not in text.lower() else 0
        return "#" if decimals == 0 or text.endswith(".0") else "#." + "#" * min(decimals, 4)
    text = str(value).strip()
    money = re.fullmatch(r"(\()?-?(\$)?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\)?", text)
    if money and (money.group(2) or "," in text or money.group(4)):
        # An amount written as text ($82,297.12, 82297.12): show its format, not its size.
        return ("$" if money.group(2) else "") + ("#,###" if "," in text else "#") + \
            ("." + "#" * min(len(money.group(4)) - 1, 4) if money.group(4) else "")
    # Character by character: a run of letters becomes A, a or Aa, a digit becomes #, any
    # whitespace one space, ASCII punctuation stays, and anything else becomes ?. So no letter,
    # digit or other character of the value can reach the output.
    out: list[str] = []
    letters = ""

    def end_of_letters() -> None:
        nonlocal letters
        if letters:
            out.append("A" if letters.isupper() else "a" if letters.islower() else "Aa")
            letters = ""

    for ch in text:
        if ch.isalpha():
            letters += ch
            continue
        end_of_letters()
        if ch.isdigit() or ch.isnumeric():
            out.append("#")
        elif ch.isspace():
            if not out or out[-1] != " ":
                out.append(" ")
        else:
            out.append(ch if ch in string.punctuation else "?")
    end_of_letters()
    text = "".join(out)
    return text if len(text) <= MAX_SHAPE_LENGTH else text[:MAX_SHAPE_LENGTH] + "..."


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "date"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (float, Decimal)):
        return "integer" if float(value).is_integer() else "decimal"
    return "text"


@dataclass
class ColumnProfile:
    id: str                      # e.g. "S1.C05": sheet 1, column 5; the model answers with these
    letter: str                  # spreadsheet column letter, for people
    header: str                  # the real header text (kept locally; withheld from the model if unsafe)
    withheld: str | None         # reason the header is not sent, or None
    filled: int
    rows: int
    types: dict[str, int]
    shapes: list[str]
    vocabularies: list[str]

    def outbound(self) -> dict:
        """The part of this profile that may leave the machine."""
        return {
            "id": self.id,
            "column": self.letter,
            "header": None if self.withheld else self.header,
            **({"header_withheld": self.withheld} if self.withheld else {}),
            "filled": f"{self.filled} of {self.rows}",
            "types": self.types,
            "shapes": self.shapes,
            **({"all_values_are": self.vocabularies} if self.vocabularies else {}),
        }


@dataclass
class SheetProfile:
    id: str
    name: str
    name_withheld: str | None
    header_row: int | None       # 1-based, as Excel shows it
    columns: list[ColumnProfile] = field(default_factory=list)

    def outbound(self) -> dict:
        return {
            "id": self.id,
            "name": None if self.name_withheld else self.name,
            **({"name_withheld": self.name_withheld} if self.name_withheld else {}),
            "header_row": self.header_row,
            "columns": [c.outbound() for c in self.columns],
        }


@dataclass
class WorkbookProfile:
    source: str
    sheets: list[SheetProfile]

    def column(self, column_id: str) -> tuple[SheetProfile, ColumnProfile] | None:
        for s in self.sheets:
            for c in s.columns:
                if c.id == column_id:
                    return s, c
        return None

    def sheet(self, sheet_id: str) -> SheetProfile | None:
        return next((s for s in self.sheets if s.id == sheet_id), None)

    def outbound(self) -> dict:
        return {"sheets": [s.outbound() for s in self.sheets]}


def profile_workbook(grid: Grid, source: str, header_check) -> WorkbookProfile:
    """Profile every sheet. header_check(text) returns a reason to withhold a header or
    sheet name (it looks like data), or None. It comes from boundary.py, so the rule that
    decides what may be sent lives in one place."""
    sheets = []
    for si, (name, rows) in enumerate(grid.sheets.items(), start=1):
        hdr = detect_header_row(rows)
        sp = SheetProfile(id=f"S{si}", name=name, name_withheld=header_check(name),
                          header_row=None if hdr is None else hdr + 1)
        if hdr is not None:
            data = [r for r in rows[hdr + 1:] if any(not _blank(v) for v in r)]
            for ci, header in enumerate(rows[hdr]):
                values = [r[ci] for r in data if ci < len(r) and not _blank(r[ci])]
                if _blank(header) and not values:
                    continue
                header_text = "" if _blank(header) else str(header).strip()
                shapes = Counter(shape(v) for v in values)
                texts = {str(v).strip().upper() for v in values if isinstance(v, str)}
                all_text = bool(values) and all(isinstance(v, str) for v in values)
                vocab = [label for label, words in VOCABULARIES.items() if all_text and texts <= words]
                sp.columns.append(ColumnProfile(
                    id=f"S{si}.C{ci + 1:02d}", letter=get_column_letter(ci + 1), header=header_text,
                    withheld=("blank header" if not header_text else header_check(header_text)),
                    filled=len(values), rows=len(data),
                    types=dict(Counter(_type_name(v) for v in values)),
                    shapes=[s for s, _ in shapes.most_common(MAX_SHAPES)],
                    vocabularies=vocab,
                ))
        sheets.append(sp)
    return WorkbookProfile(source=source, sheets=sheets)
