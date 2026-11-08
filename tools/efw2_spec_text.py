"""Read the record tables out of the text of SSA's EFW2 specification (Pub. 42-007).

The text files under specs/ssa/ were extracted from SSA's own PDFs. This module
finds each record's field table (position, field name, length, specification)
so that a layout table can be checked against the spec instead of trusted.

    python tools/efw2_spec_text.py specs/ssa/efw2_ty2025.txt          # summary
    python tools/efw2_spec_text.py specs/ssa/efw2_ty2025.txt RW       # one record's fields
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RECORD_HEADING = re.compile(r"^4\.\d+ (R[A-Z]) \(([A-Za-z ]+)\) Record\s*$")
NEXT_CHAPTER = re.compile(r"^5\.0 [A-Z]")
TABLE_HEADER = re.compile(r"^(?:POSITION )?FIELD NAME LENGTH (?:FIELD )?SPECIFICATIONS$")
PAGE_LINE = re.compile(r"^(?:Version [\d.]+: )?\d{1,2}/\d{1,2}/\d{4} (\d+|[ivx]+)\s*$")
PAGE_HEADER = ("Social Security Administration Publication No. 42-007", "EFW2 Tax Year ")
# Each page of a record table repeats the record's name above the column headings,
# e.g. "RO \n(EMPLOYEE \nOPTIONAL) \nRECORD \nPOSITION".
COLUMN_WORDS = {"RECORD", "POSITION"}


@dataclass
class SpecField:
    record: str
    start: int
    end: int
    length: int
    name: str
    page: int | None
    spec: str = ""

    @property
    def required(self) -> bool:
        return "this is a required field" in self.spec.lower()


@dataclass
class SpecRecord:
    record: str
    page: int | None
    fields: list[SpecField] = field(default_factory=list)
    overview: list[tuple[int, int]] = field(default_factory=list)  # (start, end) from the summary table


def _normalize(text: str) -> str:
    """Line endings to LF; the PDF's text layer writes some hyphens as U+0002 ("self\x02employed")."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x02", "-")


def _clean(lines: list[str]) -> str:
    text = re.sub(r"\s+", " ", " ".join(l.strip() for l in lines)).strip()
    return re.sub(r"\s*/\s*", "/", text)  # "Employer /Agent", "Foreign State/ Province" (PDF line breaks)


def same_name(a: str, b: str) -> bool:
    """Field names compared without case, spaces or punctuation ("Employer-Sponsored" = "EmployerSponsored")."""
    def key(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())
    return key(a) == key(b)


def _position(token: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,3})(?:-(\d{1,3}))?", token)
    if not m:
        return None
    start = int(m.group(1))
    return start, int(m.group(2) or start)


def parse_spec(text: str) -> dict[str, SpecRecord]:
    """Return {record type: SpecRecord} for every record table in chapter 4."""
    lines = _normalize(text).split("\n")
    records: dict[str, SpecRecord] = {}
    page: int | None = None
    current: SpecRecord | None = None
    in_table = False
    expect = 1
    open_field: SpecField | None = None
    name_lines: list[str] = []
    spec_lines: list[str] = []
    naming = False

    def close():
        nonlocal open_field, name_lines, spec_lines, naming
        if open_field is not None:
            if naming:  # never found the length: keep what we have so the caller sees the problem
                open_field.name = _clean(name_lines)
            open_field.spec = _clean(spec_lines)
            current.fields.append(open_field)
        open_field, name_lines, spec_lines, naming = None, [], [], False

    skip_column_block = False
    column_words: set[str] = set()
    bare = None
    for raw in lines:
        line = raw.strip()
        m = PAGE_LINE.match(line)
        if m:
            page = int(m.group(1)) if m.group(1).isdigit() else None
            skip_column_block = True  # the record name and column headings follow a page break
            continue
        if any(line.startswith(h) for h in PAGE_HEADER):
            continue
        heading = RECORD_HEADING.match(line)
        chapter_end = bool(records) and NEXT_CHAPTER.match(line)  # the table of contents comes first
        if heading or chapter_end:
            if current is not None:
                close()
            if heading and heading.group(1) not in records:
                current = SpecRecord(heading.group(1), page)
                records[current.record] = current
                in_table, expect, skip_column_block = False, 1, False
                column_words = COLUMN_WORDS | {current.record} | set(heading.group(2).upper().split())
                continue
            current = None
            if chapter_end:
                break
            continue
        if current is None:
            continue
        if TABLE_HEADER.match(line):
            in_table, skip_column_block = True, False
            continue
        if not in_table:
            # Summary table above the field table: rows of "Position 1-2 3-11 ..." then "Length 2 9 ...".
            # Sometimes the labels stand alone and the two rows of numbers follow them.
            tokens = line.split()
            if tokens[:1] == ["Position"]:
                if len(tokens) > 1:
                    current.overview.extend(_position(t) for t in tokens[1:] if _position(t))
                else:
                    bare = "position"
                continue
            if tokens[:1] == ["Length"]:
                bare = "positions-next" if len(tokens) == 1 and bare == "position" else None
                continue
            if bare == "positions-next" and tokens and all(_position(t) for t in tokens):
                current.overview.extend(_position(t) for t in tokens)
                bare = None
            continue
        if skip_column_block:
            if line == "" or set(line.replace("(", " ").replace(")", " ").split()) <= column_words:
                continue
            skip_column_block = False
        first, _, rest = line.partition(" ")
        span = _position(first.lstrip("*"))  # the RS table stars some positions (see its notes)
        if span and span[0] == expect and span[1] >= span[0]:
            close()
            start, end = span
            length = end - start + 1
            open_field = SpecField(current.record, start, end, length, "", page)
            expect = end + 1
            name_lines, spec_lines, naming = [], [], True
            line = rest
            if not line:
                continue
        if open_field is None:
            continue
        if naming:
            # The field name runs until the length, which is on this line or starts a later one:
            # "1-2 Record Identifier 2 Constant ..." or "3-11 Submitter's\n...\nNumber (EIN)\n9 This is ..."
            m = re.match(rf"^(.*?)(?:^|\s){open_field.length}(?:\s+(.*))?$", line)
            if m and (m.group(1).strip() or name_lines):
                name_lines.append(m.group(1))
                open_field.name = _clean(name_lines)
                naming = False
                if m.group(2):
                    spec_lines.append(m.group(2))
            else:
                name_lines.append(line)
            continue
        spec_lines.append(line)
    if current is not None:
        close()
    return records


def load_spec(path: str | Path) -> dict[str, SpecRecord]:
    return parse_spec(Path(path).read_text(encoding="utf-8"))


@dataclass
class Box12Code:
    code: str
    description: str
    record: str | None      # "RW" or "RO"; None when SSA says it "does not relate to an EFW2 field"
    start: int | None
    end: int | None


def parse_box12_chart(text: str) -> dict[str, Box12Code]:
    """Box 12 codes from Appendix E's "Paper Form W-2 and EFW2 Format Cross Reference Chart".

    Each entry reads "Code D: <description> RW Record /<field>/287-297", or ends in
    "Does not relate to an EFW2 field" for codes that have no field in the file.
    """
    lines = _normalize(text).split("\n")
    starts = [i for i, l in enumerate(lines) if l.strip().startswith("13.2 Paper Form W-2 and EFW2")]
    if not starts:
        return {}
    body = []
    for raw in lines[starts[-1] + 1:]:  # the last match is the chart itself, the first is the table of contents
        line = raw.strip()
        if re.match(r"^14\.0 APPENDIX", line):
            break
        if PAGE_LINE.match(line) or line.startswith(PAGE_HEADER) or line.startswith("PAPER FORM W-2 BOX"):
            continue
        body.append(line)
    joined = re.sub(r"\s+", " ", " ".join(body))
    parts = re.split(r"\bCode ([A-Z]{1,2}):", joined)
    codes: dict[str, Box12Code] = {}
    for code, entry in zip(parts[1::2], parts[2::2]):
        ref = re.search(r"\b(RW|RO) Record ?/.*?/ ?(\d{1,3})-(\d{1,3})", entry)
        none = entry.find("Does not relate to an EFW2 field")
        if ref and (none < 0 or ref.start() < none):
            desc = entry[:ref.start()]
            codes[code] = Box12Code(code, desc.strip(), ref.group(1), int(ref.group(2)), int(ref.group(3)))
        else:
            codes[code] = Box12Code(code, entry[:none].strip() if none >= 0 else entry.strip(), None, None, None)
    return codes


def problems(records: dict[str, SpecRecord], record_length: int = 512) -> list[str]:
    """Gaps, overlaps or disagreements between a record's summary table and its field table."""
    out = []
    for rt, rec in records.items():
        pos = 1
        for f in rec.fields:
            if f.start != pos:
                out.append(f"{rt}: field at {f.start} but expected {pos}")
            if not f.name:
                out.append(f"{rt} {f.start}-{f.end}: no field name found")
            pos = f.end + 1
        if pos - 1 != record_length:
            out.append(f"{rt}: fields end at {pos - 1}, not {record_length}")
        spans = [(f.start, f.end) for f in rec.fields]
        if not rec.overview:
            out.append(f"{rt}: no summary table found to compare the field table with")
        elif rec.overview != spans:
            extra = sorted(set(rec.overview) ^ set(spans))
            out.append(f"{rt}: summary table and field table differ at {extra[:6]}")
    return out


def main(argv: list[str]) -> int:
    records = load_spec(argv[0])
    if len(argv) > 1:
        for f in records[argv[1]].fields:
            print(f"{f.start:>3}-{f.end:<3} {f.length:>3}  p{f.page}  {'R' if f.required else ' '}  {f.name}")
        return 0
    for rt, rec in records.items():
        print(f"{rt}: {len(rec.fields)} fields from page {rec.page}, summary table spans {len(rec.overview)}")
    for p in problems(records):
        print("PROBLEM:", p)
    return 1 if problems(records) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
