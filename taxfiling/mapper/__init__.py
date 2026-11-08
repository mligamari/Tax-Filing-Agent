"""Column mapper: works out which spreadsheet column holds which W-2 field.

Data flow (see the README section "AI column mapping and the privacy boundary"):

    workbook --read_grid--> every cell, kept in memory on this computer
        |                        |
        |                  Denylist.from_grid   (every value that must not leave)
        v                        |
    profile_workbook ------------+   headers, cell types, shapes; no cell values
        |
    build_payload  ----- check #1: patterns + denylist on the payload
        |
    provider.propose --- check #2: host allow list + patterns + denylist on the exact
        |                HTTP body, inside the HTTP client; then the audit log
        v
    check_proposal  ---- the answer is untrusted: ids, fields, required fields,
        |                and each column's profile must fit its field
        v
    mapping JSON  -->  reviewed by a person  -->  `taxfiling w2 --mapping ...`
                       (reader, writer and round-trip checker run locally on full rows)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .boundary import REASONS, BoundaryViolation, Denylist, OutboundPayload, build_payload, header_problem
from .fields import catalog
from .profile import PROFILE_LABELS, WorkbookProfile, profile_workbook, read_grid
from .prompt import SYSTEM_PROMPT, response_schema
from .proposal import Review, check_proposal, format_review


@dataclass
class Prepared:
    profile: WorkbookProfile
    deny: Denylist
    payload: OutboundPayload


def fixed_words() -> set[str]:
    """Every string the program itself sends that could equal a spreadsheet label: the field
    catalog, the keys and labels of a column profile, and the literal values in the schema."""
    words = {f["field"] for f in catalog()} | {f["description"] for f in catalog()}
    return words | PROFILE_LABELS | {"high", "medium", "low", "object", "string", "array"}


def fixed_text() -> str:
    """All of the program's own text that goes into a request."""
    return "\n".join([SYSTEM_PROMPT, *sorted(fixed_words()), *REASONS])


def prepare(path: str | Path, sheets: list[str] | None = None) -> Prepared:
    """Read the workbook and build the payload. Nothing is sent."""
    grid = read_grid(path, sheets)
    deny = Denylist.from_grid(grid, public=fixed_words(), public_text=fixed_text())
    profile = profile_workbook(grid, str(path), header_problem(deny))
    payload = build_payload(profile, deny, SYSTEM_PROMPT, catalog(), response_schema(profile))
    return Prepared(profile, deny, payload)


__all__ = ["AuditLog", "BoundaryViolation", "Prepared", "Review", "check_proposal", "format_review", "prepare"]
