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
   letters with 4 or more digits (street addresses, state account numbers), or contain
   the last four digits of any 9-digit number in the workbook as a separate number.
3. Numbers: no JSON number in the request may have 5 or more digits, except max_tokens.

Error messages from this module never quote the offending value; they say where it
was and what it looked like.
"""
from __future__ import annotations

import hashlib
import json
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


class BoundaryViolation(Exception):
    """Raised instead of sending a request that would carry workbook data."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


@dataclass(frozen=True)
class Denylist:
    """Every value in the workbook that must not leave, in the forms the check compares."""
    exact: frozenset[str]           # normalized text cells of 3+ characters with a letter
    contains: tuple[str, ...]       # normalized text cells mixing letters with 4+ digits
    last4: frozenset[str]           # last four digits of every 9-digit cell (possible SSNs)
    cells: int = 0

    @classmethod
    def from_grid(cls, grid: Grid, public: set[str] = frozenset()) -> "Denylist":
        """public: strings from the program's own fixed text (field names such as 'city').
        A cell that says only 'City' is a label, not personal data, and the prompt has to
        be able to use the word."""
        public = {_norm(p) for p in public}
        exact: set[str] = set()
        contains: set[str] = set()
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
                    if isinstance(value, str) and re.search(r"[^\W\d_]", text):
                        if len(text) >= 3 and text not in public:
                            exact.add(text)
                        if len(digits) >= 4:
                            contains.add(text)
        return cls(frozenset(exact), tuple(sorted(contains)), frozenset(last4), cells)


def text_problem(text: str, deny: Denylist) -> str | None:
    """Why this string may not leave the machine, or None. Never quotes the value."""
    for pattern, label in PATTERNS:
        if pattern.search(text):
            return f"contains {label}"
    norm = _norm(text)
    if norm in deny.exact:
        return "is equal to a cell value in the workbook"
    for value in deny.contains:
        if value in norm:
            return "contains a cell value from the workbook (letters with 4+ digits, like a street address)"
    for run in re.findall(r"(?<!\d)\d{4}(?!\d)", text):
        if run in deny.last4:
            return "contains the last four digits of a 9-digit number in the workbook (possibly an SSN)"
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


def check_document(doc: Any, deny: Denylist) -> list[str]:
    """Every problem in a JSON-like document, as 'path: reason'."""
    problems = []
    for path, leaf in _walk(doc, "$"):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, (int, float)):
            key = path.rsplit(".", 1)[-1]
            if len(re.sub(r"\D", "", str(abs(int(leaf))))) >= 5 and key not in NUMBER_KEYS_ALLOWED:
                problems.append(f"{path}: a number with 5 or more digits")
            continue
        reason = text_problem(str(leaf), deny)
        if reason:
            problems.append(f"{path}: {reason}")
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
    problems = check_document({"system": system, "user": user, "schema": response_schema}, deny)
    problems += check_document(json.loads(user), deny)
    if problems:
        raise BoundaryViolation("payload refused; " + "; ".join(problems[:10]))
    digest = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()
    return OutboundPayload(system, user, response_schema, digest, _SEAL)
