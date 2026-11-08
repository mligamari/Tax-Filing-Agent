"""The privacy boundary: what may leave this computer, and the check that enforces it.

Only an OutboundPayload can be given to a model, and only build_payload() makes one.
The same check then runs a second time on the exact bytes of the HTTP request, inside
the HTTP client, after the SDK has serialized it and just before it is sent (see
providers.py). A request that fails is never sent; the program stops with
BoundaryViolation.

The check does not trust the code that built the payload. It is given every cell of
the workbook (the denylist) and looks for them in the outgoing text:

1. Patterns that are never allowed anywhere in the request: anything shaped like an
   SSN (123-45-6789 or 9 digits), any run of 5 or more digits (EINs, ZIP codes,
   account numbers, unformatted SSNs, wages), e-mail addresses and phone numbers.
2. Cell values: no string in the request may equal a cell value from below a header
   row (names, cities, codes of 3 characters or more), contain a cell value that mixes
   letters with 4 or more digits (street addresses, state account numbers), contain a
   cell value of two or more words as whole words (a full name, "Colonial Heights"), or
   contain the last four digits of any 9-digit number in the workbook as a separate
   number. A string that is itself JSON (the user message) is checked leaf by leaf too.
3. Numbers: no JSON number in the request may have 5 or more digits, except max_tokens.

Error messages from this module never quote the offending value; they say where it
was and what it looked like.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .profile import Grid, WorkbookProfile, detect_header_row

SSN_RE = re.compile(r"(?<!\d)\d{3}[-\s.]\d{2}[-\s.]\d{4}(?!\d)")
DIGIT_RUN_RE = re.compile(r"\d{5,}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
PATTERNS = [
    (SSN_RE, "something shaped like an SSN"),
    (DIGIT_RUN_RE, "a run of 5 or more digits (SSN, EIN, ZIP code, account number or amount)"),
    (EMAIL_RE, "an e-mail address"),
    (PHONE_RE, "a phone number"),
]
NUMBER_KEYS_ALLOWED = {"max_tokens"}
REASON_EQUAL = "is equal to a cell value in the workbook"
REASON_CONTAINS = "contains a cell value from the workbook (letters with 4+ digits, like a street address)"
REASON_PHRASE = "contains a cell value of two or more words from the workbook (like a full name)"
REASON_LAST4 = "contains the last four digits of a 9-digit number in the workbook (possibly an SSN)"
# Every reason text, which the profile sends when it withholds a header (so they are fixed text).
REASONS = [f"contains {label}" for _, label in PATTERNS] + [REASON_EQUAL, REASON_CONTAINS, REASON_PHRASE,
                                                             REASON_LAST4]


class BoundaryViolation(Exception):
    """Raised instead of sending a request that would carry workbook data."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _alternation(values, whole_words: bool) -> re.Pattern | None:
    """One compiled pattern that finds any of the values (longest first), or None."""
    if not values:
        return None
    body = "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)" if whole_words else body)


@dataclass(frozen=True)
class Denylist:
    """Every value in the workbook that must not leave, in the forms the check compares."""
    exact: frozenset[str]               # normalized text cells of 3+ characters with a letter
    contains: re.Pattern | None         # text cells mixing letters with 4+ digits (street addresses)
    phrases: re.Pattern | None          # text cells of 2+ words (full names, cities), as whole words
    last4: frozenset[str]               # last four digits of every 9-digit cell (possible SSNs)
    cells: int = 0

    @classmethod
    def from_grid(cls, grid: Grid, public: set[str] = frozenset(), public_text: str = "") -> "Denylist":
        """public: strings the program itself sends (field names such as 'city', the labels in
        a column profile). A cell that says only 'City' is a label, not personal data, and the
        prompt has to be able to use the word. public_text: the program's fixed text (the
        instructions and the field list); a multi-word cell value found in it, such as
        'Social Security', is not treated as data."""
        public = {_norm(p) for p in public}
        fixed = _norm(public_text)
        exact: set[str] = set()
        contains: set[str] = set()
        phrases: set[str] = set()
        last4: set[str] = set()
        cells = 0
        for rows in grid.sheets.values():
            hdr = detect_header_row(rows)
            for i, row in enumerate(rows):
                if i == hdr:
                    continue        # the header row is what the profile describes
                for value in row:
                    if value is None or isinstance(value, bool):
                        continue
                    cells += 1
                    text = _norm(value)
                    digits = re.sub(r"\D", "", text)
                    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
                        digits = str(int(value))
                    if len(digits) == 9 or (isinstance(value, (int, float)) and 7 <= len(digits) <= 9):
                        last4.add(digits[-4:])     # 9-digit IDs; SSNs that lost leading zeros
                    if not isinstance(value, str) or not re.search(r"[^\W\d_]", text):
                        continue
                    if len(text) >= 3 and text not in public:
                        exact.add(text)
                    if len(digits) >= 4:
                        contains.add(text)
                    elif " " in text and len(text) >= 5 and text not in public and \
                            not re.search(rf"(?<!\w){re.escape(text)}(?!\w)", fixed):
                        phrases.add(text)
        return cls(frozenset(exact), _alternation(contains, False), _alternation(phrases, True),
                   frozenset(last4), cells)


def text_problem(text: str, deny: Denylist) -> str | None:
    """Why this string may not leave the machine, or None. Never quotes the value."""
    for pattern, label in PATTERNS:
        if pattern.search(text):
            return f"contains {label}"
    norm = _norm(text)
    if norm in deny.exact:
        return REASON_EQUAL
    if deny.contains and deny.contains.search(norm):
        return REASON_CONTAINS
    if deny.phrases and deny.phrases.search(norm):
        return REASON_PHRASE
    for run in re.findall(r"(?<!\d)\d{4}(?!\d)", text):
        if run in deny.last4:
            return REASON_LAST4
    return None


def header_problem(deny: Denylist):
    """The rule the profiler uses to withhold a header or sheet name: the same check the
    boundary applies, so a header that would be blocked is left out instead."""
    return lambda text: text_problem(text, deny)


def _walk(node: Any, path: str):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(k, f"{path}.<key>")
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def _embedded_json(text: str) -> Any:
    """A string that is itself JSON (the user message is), parsed; otherwise None."""
    stripped = text.strip()
    if stripped[:1] not in ("{", "["):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def check_document(doc: Any, deny: Denylist, root: str = "$") -> list[str]:
    """Every problem in a JSON-like document, as 'path: reason'. A string that is itself
    JSON is checked as a whole and then leaf by leaf, so a header inside the user message
    gets the same checks on the wire as it got when the payload was built."""
    problems = []
    for path, leaf in _walk(doc, root):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, (int, float)):
            key = path.rsplit(".", 1)[-1]
            if isinstance(leaf, float) and not math.isfinite(leaf):
                problems.append(f"{path}: a number that is not finite")
            elif len(str(abs(int(leaf)))) >= 5 and key not in NUMBER_KEYS_ALLOWED:
                problems.append(f"{path}: a number with 5 or more digits")
            continue
        reason = text_problem(str(leaf), deny)
        if reason:
            problems.append(f"{path}: {reason}")
        inner = _embedded_json(str(leaf))
        if inner is not None:
            problems += check_document(inner, deny, f"{path}<json>")
    return problems


def check_wire_body(body: bytes, deny: Denylist) -> None:
    """The last check, on the exact bytes about to be sent. Raises BoundaryViolation."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BoundaryViolation("the request body is not JSON, so it cannot be checked; not sent") from None
    problems = check_document(doc, deny)
    if problems:
        raise BoundaryViolation("request blocked before sending; " + "; ".join(problems[:10]))


_SEAL = object()


@dataclass(frozen=True)
class OutboundPayload:
    """The only thing a provider may send. Build it with build_payload()."""
    system: str
    user: str
    response_schema: dict
    sha256: str
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._seal is not _SEAL:
            raise TypeError("OutboundPayload can only be made by build_payload(), which checks it")


def _render(doc: dict) -> str:
    """Compact JSON with one field and one column per line, so a person can read the log."""
    def line(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)
    sheets = []
    for sheet in doc["workbook"]["sheets"]:
        head = {k: v for k, v in sheet.items() if k != "columns"}
        cols = ",\n".join("   " + line(c) for c in sheet["columns"])
        sheets.append(f"  {line(head)[:-1]}, \"columns\": [\n{cols}\n  ]}}")
    fields = ",\n".join("  " + line(f) for f in doc["fields"])
    return '{"fields": [\n' + fields + '\n ],\n "workbook": {"sheets": [\n' + ",\n".join(sheets) + "\n ]}\n}"


def build_payload(profile: WorkbookProfile, deny: Denylist, system: str, catalog: list[dict],
                  response_schema: dict) -> OutboundPayload:
    """Turn a workbook profile into the text that may be sent, after checking it."""
    user = _render({"fields": catalog, "workbook": profile.outbound()})
    # The user message is JSON inside a string, so check_document also checks each header in it.
    problems = check_document({"system": system, "user": user, "schema": response_schema}, deny)
    if problems:
        raise BoundaryViolation("payload refused; " + "; ".join(problems[:10]))
    digest = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()
    return OutboundPayload(system, user, response_schema, digest, _SEAL)
