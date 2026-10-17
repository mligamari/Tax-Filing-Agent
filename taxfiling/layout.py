"""Fixed-width record layouts, loaded from JSON tables.

Nothing in this module knows about EFW2 or ICESA. A layout file lists, for every
record type, each field's position, type and padding rule; this module turns a
dict of values into a fixed-width line and back again. Format-specific code
(formats/efw2.py, later formats/icesa.py) only decides which values go in.

Field types:
  const  literal value from the table ("value"), e.g. the record identifier
  A      alphanumeric, left-justified, blank-filled, uppercased unless case="mixed"
  N      numeric, right-justified, zero-filled
  M      money in dollars and cents, written as whole cents (implied decimal)
  MW     money in whole dollars
  blank  filler, always spaces
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

LAYOUT_DIR = Path(__file__).parent / "layouts"


class LayoutError(ValueError):
    """A value cannot be written into its field (too long, wrong kind, negative)."""


@dataclass(frozen=True)
class Field:
    key: str
    name: str
    start: int
    end: int
    type: str
    required: bool
    value: str | None = None
    case: str = "upper"
    empty: str = "zero"  # how an N/M/MW field with no value is written: "zero" or "blank"
    verified: bool = True
    sum_of: str | None = None
    count_of: str | None = None
    sources: tuple[str, ...] = ()
    notes: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def slice(self) -> slice:
        return slice(self.start - 1, self.end)


@dataclass
class RecordLayout:
    record_type: str
    name: str
    fields: list[Field]
    _by_key: dict[str, Field] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._by_key = {f.key: f for f in self.fields}

    def __getitem__(self, key: str) -> Field:
        return self._by_key[key]

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def keys(self) -> list[str]:
        return [f.key for f in self.fields]


@dataclass
class Layout:
    format: str
    title: str
    record_length: int
    line_ending: str
    records: dict[str, RecordLayout]
    sources: dict[str, dict]
    tax_year: int | None = None

    def __getitem__(self, record_type: str) -> RecordLayout:
        return self.records[record_type]

    def unverified_fields(self) -> list[tuple[str, Field]]:
        """Fields whose positions were not confirmed against a published spec."""
        out = []
        for rt, rec in self.records.items():
            for f in rec.fields:
                if not f.verified or f.sources == ("recall",):
                    out.append((rt, f))
        return out


def load_layout(name_or_path: str | Path) -> Layout:
    path = Path(name_or_path)
    if not path.suffix:
        path = LAYOUT_DIR / f"{name_or_path}.json"
    data = json.loads(path.read_text())
    records = {}
    for rt, rec in data["records"].items():
        fields = [
            Field(
                key=f["key"], name=f["name"], start=f["start"], end=f["end"], type=f["type"],
                required=f.get("required") == "Y", value=f.get("value"), case=f.get("case", "upper"),
                empty=f.get("empty", "zero"), verified=f.get("verified", True), sum_of=f.get("sum_of"),
                count_of=f.get("count_of"), sources=tuple(f.get("sources", ())), notes=f.get("notes", ""),
            )
            for f in rec["fields"]
        ]
        records[rt] = RecordLayout(rt, rec.get("name", rt), fields)
    layout = Layout(
        format=data["format"], title=data.get("title", ""), record_length=data["record_length"],
        line_ending=data.get("line_ending", "\r\n"), records=records, sources=data.get("sources", {}),
        tax_year=data.get("tax_year"),
    )
    check_layout(layout)
    return layout


def check_layout(layout: Layout) -> None:
    """Every record's fields must be contiguous and add up to the record length."""
    for rt, rec in layout.records.items():
        pos = 1
        for f in rec.fields:
            if f.start != pos or f.end < f.start:
                raise LayoutError(f"{rt}.{f.key}: starts at {f.start}, expected {pos}")
            pos = f.end + 1
        if pos - 1 != layout.record_length:
            raise LayoutError(f"{rt}: fields end at {pos - 1}, record length is {layout.record_length}")
        if len(rec._by_key) != len(rec.fields):
            raise LayoutError(f"{rt}: duplicate field keys")


def _cents(value: Any) -> int:
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(d * 100)


def _shown(f: Field, value: Any) -> str:
    """A value as it may appear in an error message: SSN fields are never echoed."""
    return "(value hidden)" if "ssn" in f.key else f"'{value}'"


def format_field(f: Field, value: Any, record_type: str = "") -> str:
    where = f"{record_type}.{f.key} ({f.name}, positions {f.start}-{f.end})"
    if f.type == "blank":
        return " " * f.length
    if f.type == "const":
        return (f.value or "").ljust(f.length)
    if value is None or value == "":
        if f.type in ("N", "M", "MW") and f.empty == "zero":
            return "0" * f.length
        return " " * f.length
    if f.type == "A":
        text = str(value)
        if f.case != "mixed":
            text = text.upper()
        if len(text) > f.length:
            raise LayoutError(f"{where}: {_shown(f, text)} is {len(text)} characters, field holds {f.length}")
        if not text.isascii():
            raise LayoutError(f"{where}: {_shown(f, text)} has non-ASCII characters")
        return text.ljust(f.length)
    if f.type == "N":
        text = str(value)
        if not text.isdigit():
            raise LayoutError(f"{where}: {_shown(f, text)} is not all digits")
    elif f.type == "M":
        cents = _cents(value)
        if cents < 0:
            raise LayoutError(f"{where}: negative amount {value}")
        text = str(cents)
    elif f.type == "MW":
        d = Decimal(str(value))
        if d != d.to_integral_value():
            raise LayoutError(f"{where}: {value} is not whole dollars")
        if d < 0:
            raise LayoutError(f"{where}: negative amount {value}")
        text = str(int(d))
    else:
        raise LayoutError(f"{where}: unknown field type {f.type}")
    if len(text) > f.length:
        raise LayoutError(f"{where}: {_shown(f, value)} does not fit in {f.length} digits")
    return text.rjust(f.length, "0")


def encode_record(rec: RecordLayout, values: dict[str, Any], record_length: int) -> str:
    unknown = set(values) - set(rec.keys())
    if unknown:
        raise LayoutError(f"{rec.record_type}: no such field(s) {sorted(unknown)}")
    line = "".join(format_field(f, values.get(f.key), rec.record_type) for f in rec.fields)
    assert len(line) == record_length, (rec.record_type, len(line))
    return line


def parse_field(f: Field, raw: str) -> Any:
    """Inverse of format_field. Blank N/M fields come back as None."""
    if f.type in ("blank", "const"):
        return raw
    if f.type == "A":
        return raw.rstrip()
    if raw.strip() == "":
        return None
    if not raw.isdigit():
        raise LayoutError(f"{f.name} (positions {f.start}-{f.end}): {_shown(f, raw)} is not numeric")
    if f.type == "N":
        return raw
    if f.type == "M":
        return Decimal(int(raw)) / 100
    if f.type == "MW":
        return Decimal(int(raw))
    raise LayoutError(f"{f.key}: unknown field type {f.type}")


def decode_record(rec: RecordLayout, line: str) -> dict[str, Any]:
    return {f.key: parse_field(f, line[f.slice]) for f in rec.fields}
