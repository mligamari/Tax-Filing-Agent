"""What the demo screen does, without the web server: the same pipeline as the command line.

    upload a workbook  ->  analyze()   profile + the exact payload a model would get (nothing sent)
                       ->  propose()   offline rules or Claude propose a column mapping; audit log
                       ->  build()     the person's reviewed mapping -> reader -> writer -> round-trip
                                       check, all on this computer; the file is offered only if
                                       the check passed

Everything returned to the browser has SSNs masked. The only place a full SSN appears is
the finished file itself, which the state requires.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..check import check_file
from ..check_quarterly import check_quarterly_file
from ..config import apply_employer_settings, load_submitter
from ..formats.efw2 import build_efw2
from ..formats.efw2_quarterly import build_efw2_quarterly
from ..issues import Issues
from ..layout import load_layout
from ..mapper import AuditLog, BoundaryViolation, check_proposal, prepare
from ..mapper.fields import FIELDS
from ..model import WageReport
from ..readers.excel import read_w2_workbook
from ..readers.quarterly import load_quarterly_report
from ..states import report_profile
from ..validate import validate_report

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
YEARS = (2025, 2026)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

SAMPLES = {
    "va_2025": {
        "title": "Virginia employer, tax year 2025",
        "detail": "50 synthetic employees in a tidy W-2 workbook, with a Quarterly Wages sheet.",
        "path": EXAMPLES / "va_2025" / "w2_2025_va_synthetic.xlsx", "year": 2025,
    },
    "va_2025_messy": {
        "title": "Messy payroll export, tax year 2025",
        "detail": "The same people exported with short, odd headers ('Fed W/H', 'St', 'Work State'). "
                  "The rules leave two columns for you to choose.",
        "path": EXAMPLES / "va_2025_messy" / "w2_2025_va_messy.xlsx", "year": 2025,
    },
    "va_2026": {
        "title": "Virginia employer, tax year 2026",
        "detail": "The 2026 version of the tidy workbook, written with SSA's 2026 layout.",
        "path": EXAMPLES / "va_2026" / "w2_2026_va_synthetic.xlsx", "year": 2026,
    },
}

OUTPUTS = {
    "w2": {"title": "Virginia annual W-2 file", "agency": "Virginia Tax, Web Upload", "provisional": False},
    "ui": {"title": "Virginia quarterly unemployment-insurance wage file",
           "agency": "Virginia Employment Commission (VEC)", "provisional": True},
}


class DemoError(Exception):
    """A problem to show the person, in plain words (with the audit log, if a request was made)."""

    def __init__(self, message: str, audit: list | None = None):
        super().__init__(message)
        self.audit = audit


def claude_status() -> dict:
    """Can the Claude option be offered? Checked without importing the SDK."""
    import importlib.util

    if importlib.util.find_spec("anthropic") is None:
        return {"available": False,
                "reason": "The anthropic package is not installed (python -m pip install -e \".[ai]\")."}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"available": False, "reason": "ANTHROPIC_API_KEY is not set in the terminal that started the demo."}
    return {"available": True, "reason": ""}


@dataclass
class Session:
    id: str
    folder: Path
    workbook: Path
    display_name: str
    default_year: int
    prepared: object = None
    review: object = None
    provider: str = ""
    audit: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)     # download id -> (file name, bytes)


class Demo:
    """All state lives in one temporary folder, removed when the demo stops."""

    def __init__(self):
        self.folder = Path(tempfile.mkdtemp(prefix="taxfiling-demo-"))
        self.sessions: dict[str, Session] = {}

    def close(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    # --- step 1: a workbook ---------------------------------------------------------

    def open_sample(self, key: str) -> dict:
        sample = SAMPLES.get(key)
        if sample is None:
            raise DemoError(f"unknown sample {key!r}")
        if not sample["path"].exists():
            raise DemoError("The sample workbooks live in the repository's examples folder. Run the demo from a "
                            "clone of the repository (python -m pip install -e .).")
        return self._open(sample["path"].read_bytes(), sample["path"].name, sample["year"])

    def open_upload(self, data: bytes, name: str) -> dict:
        if len(data) > MAX_UPLOAD_BYTES:
            raise DemoError("That file is larger than 10 MB; a W-2 workbook is much smaller.")
        if not data.startswith(b"PK"):
            raise DemoError("That does not look like an Excel workbook (.xlsx).")
        return self._open(data, Path(name).name or "upload.xlsx", YEARS[0])

    def _open(self, data: bytes, name: str, year: int) -> dict:
        sid = secrets.token_hex(8)
        folder = self.folder / sid
        folder.mkdir()
        workbook = folder / "input.xlsx"
        workbook.write_bytes(data)
        session = Session(sid, folder, workbook, name, year)
        try:
            session.prepared = prepare(workbook)
        except BoundaryViolation as exc:
            raise DemoError(f"Stopped at the privacy boundary: {exc}") from None
        except Exception as exc:  # openpyxl raises many kinds of errors on a damaged file
            raise DemoError(f"Could not read that workbook: {exc}") from None
        self.sessions[sid] = session
        return self._describe(session)

    def _describe(self, s: Session) -> dict:
        profile, payload = s.prepared.profile, s.prepared.payload
        return {
            "session": s.id, "name": s.display_name, "default_year": s.default_year,
            "sheets": [{"id": sp.id, "name": sp.name, "header_row": sp.header_row,
                        "columns": [{"id": c.id, "letter": c.letter, "header": c.header, "shapes": c.shapes,
                                     "filled": c.filled}
                                    for c in sp.columns if c.header]}
                       for sp in profile.sheets],
            "fields": [{"key": f.key, "description": f.description, "required": f.required} for f in FIELDS],
            "payload": {"system": payload.system, "user": payload.user, "sha256": payload.sha256,
                        "characters": len(payload.system) + len(payload.user),
                        "cells_checked": s.prepared.deny.cells},
            "claude": claude_status(),
        }

    def session(self, sid: str) -> Session:
        s = self.sessions.get(str(sid))
        if s is None:
            raise DemoError("This page is out of date (the demo was restarted). Choose the spreadsheet again.")
        return s

    # --- step 2: a proposed mapping -------------------------------------------------

    def propose(self, sid: str, ai: str) -> dict:
        from ..mapper.providers import ClaudeProvider, OfflineProvider, ProviderError

        s = self.session(sid)
        audit = AuditLog(s.folder / "mapping-audit.jsonl")
        try:
            if ai == "none":
                provider = OfflineProvider(audit)
            elif ai == "claude":
                status = claude_status()
                if not status["available"]:
                    raise DemoError(status["reason"])
                provider = ClaudeProvider(s.prepared.deny, audit)
            else:
                raise DemoError(f"unknown mode {ai!r}")
            answer = provider.propose(s.prepared.payload, s.prepared.profile)
        except BoundaryViolation as exc:
            raise DemoError(f"Stopped at the privacy boundary, nothing was sent: {exc}", audit.entries) from None
        except ProviderError as exc:
            raise DemoError(str(exc), audit.entries) from None
        finally:
            s.audit = audit.entries
        mapping, review = check_proposal(answer, s.prepared.profile)
        s.review, s.provider = review, provider.name
        sheet = s.prepared.profile.sheet(answer.get("sheet", "")) if isinstance(answer, dict) else None
        by_header = {c.header: c.id for c in sheet.columns} if sheet else {}
        return {
            "provider": provider.name, "endpoint": provider.endpoint,
            "sheet": sheet.id if sheet else None,
            "columns": {r["field"]: {"column": by_header.get(r["header"]), "confidence": r["confidence"],
                                     "reason": r["reason"], "check": r["check"]} for r in review.rows},
            "box12": [{"code": by_header.get(p["code"]), "amount": by_header.get(p["amount"])}
                      for p in (mapping or {}).get("box12", [])],
            "notes": [_letters(n, s.prepared.profile) for n in review.notes],
            "warnings": [_letters(w, s.prepared.profile) for w in review.warnings],
            "errors": [_letters(e, s.prepared.profile) for e in review.errors],
            "missing_required": review.missing_required,
            "audit": s.audit,
        }

    # --- step 3: the file -----------------------------------------------------------

    def _reviewed_mapping(self, s: Session, choice: dict) -> dict:
        """The person's mapping, put through the same checks as a model's answer."""
        answer = {
            "sheet": choice.get("sheet"),
            "columns": [{"field": k, "column": v, "confidence": "high", "reason": "reviewed by a person"}
                        for k, v in (choice.get("columns") or {}).items() if v],
            "box12": [{"code_column": p.get("code"), "amount_column": p.get("amount")}
                      for p in choice.get("box12") or [] if p.get("code") and p.get("amount")],
        }
        mapping, review = check_proposal(answer, s.prepared.profile)
        if mapping is None:
            raise DemoError("The mapping has a problem: " + "; ".join(review.errors))
        if review.missing_required:
            raise DemoError("Choose a column for every required field first: " + ", ".join(review.missing_required))
        return mapping

    def build(self, sid: str, choice: dict) -> dict:
        s = self.session(sid)
        kind = choice.get("output")
        if kind not in OUTPUTS:
            raise DemoError("Choose which file to make.")
        year = int(choice.get("year") or s.default_year)
        if year not in YEARS:
            raise DemoError(f"Tax year must be one of {', '.join(map(str, YEARS))}.")
        mapping = self._reviewed_mapping(s, choice)
        submitter_file = EXAMPLES / f"va_{year}" / "submitter.json"
        try:
            if kind == "w2":
                return self._build_w2(s, mapping, year, submitter_file)
            quarter = int(choice.get("quarter") or 1)
            if quarter not in (1, 2, 3, 4):
                raise DemoError("Quarter must be 1 to 4.")
            return self._build_ui(s, mapping, year, quarter, submitter_file)
        except (ValueError, KeyError) as exc:
            raise DemoError(str(exc).strip("'\"")) from None

    def _result(self, s: Session, kind: str, profile: dict, layout, issues: Issues, result, data: bytes | None,
                file_name: str, employees: int, extra: dict | None = None) -> dict:
        download = None
        if data is not None and result is not None and result.ok:
            download = secrets.token_hex(8)
            s.outputs[download] = (file_name, data)
        check = None
        if result is not None:
            check = {"ok": result.ok, "records": dict(result.record_counts),
                     "employees_compared": result.employees_compared, "fields_compared": result.fields_compared,
                     "totals": {k: f"{v:,.2f}" for k, v in result.totals.items()},
                     "errors": [str(i) for i in result.issues.errors],
                     "warnings": [str(i) for i in result.issues.warnings]}
        return {
            "output": kind, "title": OUTPUTS[kind]["title"], "agency": profile.get("agency"),
            "provisional": bool(profile.get("provisional")), "year": profile.get("tax_year"),
            "employees": employees, "file_name": file_name,
            "bytes": len(data) if data is not None else 0,
            "input_errors": [str(i) for i in issues.errors],
            "input_warnings": [str(i) for i in issues.warnings],
            "check": check, "download": download,
            "preview": masked_preview(data, layout) if download else [],
            "provider": s.provider, **(extra or {}),
        }

    def _build_w2(self, s: Session, mapping: dict, year: int, submitter_file: Path) -> dict:
        profile = report_profile("VA", "w2", year)
        layout = load_layout(profile["layout"])
        employer, employees, issues = read_w2_workbook(s.workbook, mapping)
        submitter, settings = load_submitter(submitter_file)
        apply_employer_settings(employer, settings, submitter)
        report = WageReport(tax_year=year, submitter=submitter, employer=employer, employees=employees)
        name = profile["default_file_name"].format(year=year)
        if not issues.errors:
            issues.extend(validate_report(report))
        if issues.errors:
            return self._result(s, "w2", profile, layout, issues, None, None, name, len(employees))
        text, build_issues = build_efw2(report, layout, profile)
        issues.extend(build_issues)
        if text is None:
            return self._result(s, "w2", profile, layout, issues, None, None, name, len(employees))
        data = text.encode("ascii")
        result = check_file(data, layout, profile, source=report)   # checked before it is offered
        return self._result(s, "w2", profile, layout, issues, result, data, name, len(employees))

    def _build_ui(self, s: Session, mapping: dict, year: int, quarter: int, submitter_file: Path) -> dict:
        profile = report_profile("VA", "ui_quarterly", year)
        layout = load_layout(profile["layout"])
        name = profile["default_file_name"].format(year=year, quarter=quarter)
        try:
            report, issues = load_quarterly_report(s.workbook, "VA", year, quarter, None, mapping)
        except ValueError as exc:
            if "no sheet named" in str(exc):
                raise DemoError("This workbook has no 'Quarterly Wages' sheet, so it cannot make a quarterly "
                                "file. Try the tidy Virginia sample, or make the annual W-2 file.") from None
            raise
        submitter, settings = load_submitter(submitter_file)
        apply_employer_settings(report.employer, settings, submitter)
        report.submitter = submitter
        extra = {"quarter": quarter, "employed_12th": list(report.employed_12th_counts)}
        if issues.errors:
            return self._result(s, "ui", profile, layout, issues, None, None, name, len(report.employees), extra)
        text, build_issues = build_efw2_quarterly(report, layout, profile)
        issues.extend(build_issues)
        if text is None:
            return self._result(s, "ui", profile, layout, issues, None, None, name, len(report.employees), extra)
        data = text.encode("ascii")
        result = check_quarterly_file(data, layout, profile, year, quarter, source=report)
        return self._result(s, "ui", profile, layout, issues, result, data, name, len(report.employees), extra)

    def download(self, sid: str, did: str) -> tuple[str, bytes]:
        s = self.session(sid)
        if did not in s.outputs:
            raise DemoError("That file is no longer available; make it again.")
        return s.outputs[did]


def _letters(text: str, profile) -> str:
    """Column ids such as S1.C09 (the model's names for columns) as people see them: I."""
    def name(m):
        found = profile.column(m.group(0))
        return found[1].letter if found else m.group(0)
    return re.sub(r"\bS\d+\.C\d+\b", name, text)


def masked_preview(data: bytes, layout, per_type: int = 2) -> list[dict]:
    """A few records of the file as written (up to two of each type), every SSN masked."""
    shown, count = [], {}
    for line in data.decode("ascii").replace("\r\n", "\n").split("\n"):
        rt = line[:2]
        count[rt] = count.get(rt, 0) + 1
        if count[rt] > per_type:
            continue
        if rt in layout.records:
            for f in layout[rt].fields:
                if f.key == "ssn":
                    digits = line[f.start - 1:f.end]
                    line = line[:f.start - 1] + ("*" * 5 + digits[-4:]).rjust(len(digits)) + line[f.end:]
        shown.append({"record": rt, "name": layout[rt].name if rt in layout.records else "", "text": line.rstrip()})
    return shown


def to_json(obj) -> bytes:
    return json.dumps(obj, default=str).encode("utf-8")
