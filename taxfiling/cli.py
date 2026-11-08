"""Command line: write a state W-2 file, check a file, show a file, list a layout.

    python -m taxfiling w2 --state VA --input W2.xlsx --submitter submitter.json [--year 2025]
    python -m taxfiling check "W2 REPORT 2025.txt" --state VA --input W2.xlsx
    python -m taxfiling show "W2 REPORT 2025.txt" --record RW --limit 2
    python -m taxfiling layout efw2_ty2025 --record RO

Everything runs locally. Nothing here opens a network connection, and SSNs are
masked in everything printed (the output file itself holds full SSNs, as the
state requires).
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from .check import check_file, format_report
from .config import apply_employer_settings, load_submitter
from .formats.efw2 import build_efw2
from .layout import available_layouts, load_layout
from .model import WageReport
from .privacy import mask_ssn
from .readers.excel import load_mapping, read_w2_workbook
from .states import available_states, report_profile
from .validate import validate_report


def _print_issues(title: str, issues, limit: int | None = None) -> None:
    if not issues:
        return
    print(f"{title}: {len(issues.errors)} error(s), {len(issues.warnings)} warning(s)")
    items = issues.errors + issues.warnings
    for issue in items[:limit]:
        print(f"  {issue}")
    if limit and len(items) > limit:
        print(f"  ... {len(items) - limit} more")


def _load_source(args, layout, need_submitter: bool) -> tuple[WageReport, object]:
    mapping = load_mapping(args.mapping)
    employer, employees, read_issues = read_w2_workbook(args.input, mapping)
    submitter = None
    if args.submitter:
        submitter, employer_settings = load_submitter(args.submitter)
        apply_employer_settings(employer, employer_settings, submitter)
    elif need_submitter:
        raise SystemExit("--submitter is required to write a file (it supplies the RA record)")
    report = WageReport(tax_year=args.year or layout.tax_year, submitter=submitter, employer=employer,
                        employees=employees)
    return report, read_issues


def _year_in_file(path: Path) -> int | None:
    """The tax year in a wage file's first RE record (positions 3-6), if there is one."""
    for line in path.read_bytes().decode("ascii", errors="replace").splitlines():
        if line.startswith("RE") and line[2:6].isdigit():
            return int(line[2:6])
    return None


def _layout_for(args, path: Path | None = None) -> str:
    """--layout if given, else the layout for --year, else for the tax year written in the file."""
    if getattr(args, "layout", None):
        return args.layout
    year = getattr(args, "year", None) or (_year_in_file(path) if path else None)
    name = f"efw2_ty{year}"
    if name not in available_layouts():
        raise ValueError(f"no layout for tax year {year}; available: {', '.join(available_layouts())} "
                         "(choose one with --layout)")
    return name


def cmd_w2(args) -> int:
    profile = report_profile(args.state, "w2", args.year)
    layout = load_layout(profile["layout"])
    report, issues = _load_source(args, layout, need_submitter=True)
    print(f"Read {len(report.employees)} employees for {report.employer.name} from {args.input}")

    issues.extend(validate_report(report))
    if issues.errors:
        _print_issues("Input problems", issues)
        print("No file written.")
        return 1

    text, build_issues = build_efw2(report, layout, profile)
    issues.extend(build_issues)
    _print_issues("Input notes", issues, limit=args.max_messages)
    if text is None:
        print("No file written.")
        return 1

    # Check the exact bytes before anything reaches the disk, so a file that fails its
    # check never sits in the folder under the name the state expects.
    data = text.encode("ascii")
    result = check_file(data, layout, profile, source=report)
    out = Path(args.out or profile["default_file_name"].format(year=report.tax_year))
    if not result.ok:
        print(format_report(result, title="Round-trip check"))
        if out.exists():
            print(f"No file written: the round-trip check failed. {out} is an older file from an earlier "
                  "run and was NOT updated; do not upload it.")
        else:
            print(f"No file written: the round-trip check failed, so {out} was not created.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data):,} bytes) for {profile['state_name']} "
          f"({profile['agency']}, {profile['portal']})")
    print(format_report(result, title="Round-trip check"))
    return 0


def cmd_check(args) -> int:
    path = Path(args.file)
    year = args.year or _year_in_file(path)
    profile = report_profile(args.state, "w2", year) if args.state else {}
    layout = load_layout(args.layout or profile.get("layout") or _layout_for(args, path))
    source = None
    if args.input:
        source, read_issues = _load_source(args, layout, need_submitter=False)
        _print_issues("Input notes", read_issues, limit=args.max_messages)
    result = check_file(path, layout, profile, source=source)
    print(format_report(result, title=f"Check of {args.file}"))
    return 0 if result.ok else 1


def cmd_show(args) -> int:
    layout = load_layout(_layout_for(args, Path(args.file)))
    data = Path(args.file).read_bytes().decode("ascii", errors="replace")
    lines = data.replace("\r\n", "\n").rstrip("\n").split("\n")
    shown = 0
    for i, line in enumerate(lines, 1):
        rt = line[:2]
        if args.record and rt != args.record.upper():
            continue
        if args.limit and shown >= args.limit:
            break
        shown += 1
        print(f"--- line {i}: {rt} ({len(line)} characters)")
        if rt not in layout.records:
            print("    (no layout loaded for this record type)")
            continue
        for f in layout[rt].fields:
            raw = line[f.start - 1:f.end]
            if f.type in ("blank", "const") or not raw.strip() or (f.type in ("M", "N") and not raw.strip("0")):
                continue
            value = raw.rstrip()
            if f.key == "ssn" and not args.show_ssn:
                value = mask_ssn(value)
            elif f.type == "M":
                value = f"{Decimal(int(raw)).scaleb(-2):,.2f}"
            print(f"    {f.start:>3}-{f.end:<3} {f.name:<55} {value}")
    return 0


def cmd_layout(args) -> int:
    layout = load_layout(args.name)
    print(f"{layout.title}: {layout.record_length}-character records")
    unverified = {(rt, f.key) for rt, f in layout.unverified_fields()}
    for rt, rec in layout.records.items():
        if args.record and rt != args.record.upper():
            continue
        fields = [f for f in rec.fields if not args.unverified or (rt, f.key) in unverified]
        if fields:
            print(f"\n{rt}  {rec.name}")
        for f in fields:
            flag = "  UNVERIFIED" if (rt, f.key) in unverified else ""
            page = f" p.{f.page}" if f.page else ""
            print(f"  {f.start:>3}-{f.end:<3} {f.length:>3} {f.type:<5} {f.key:<30} {f.name[:60]:<60} "
                  f"[{', '.join(f.sources)}{page}]{flag}")
    print(f"\n{len(unverified)} field(s) not confirmed against a published spec. Sources:")
    for key, src in layout.sources.items():
        print(f"  {key}: {src['title']} {src.get('url', '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="taxfiling", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def source_args(sp, required: bool):
        sp.add_argument("--input", required=required, help="W-2 workbook (.xlsx)")
        sp.add_argument("--mapping", default="w2_standard",
                        help="column mapping name or JSON path (default: w2_standard)")
        sp.add_argument("--submitter", help="submitter/contact settings JSON (RA record)")
        sp.add_argument("--year", type=int,
                        help="tax year (w2: default is the state's default year; check: the year in the file)")
        sp.add_argument("--max-messages", type=int, default=40, help="cap on printed input notes")

    w2 = sub.add_parser("w2", help="write a state W-2 file and check it")
    w2.add_argument("--state", required=True, choices=available_states())
    source_args(w2, required=True)
    w2.add_argument("--out", help="output path (default: the state's recommended file name)")
    w2.set_defaults(func=cmd_w2)

    ck = sub.add_parser("check", help="check a file; with --input, also compare it with the source")
    ck.add_argument("file")
    ck.add_argument("--state", choices=available_states())
    ck.add_argument("--layout", help="layout name or path (default: the one for the file's tax year)")
    source_args(ck, required=False)
    ck.set_defaults(func=cmd_check)

    sh = sub.add_parser("show", help="print a file field by field (SSNs masked)")
    sh.add_argument("file")
    sh.add_argument("--layout", help="layout name or path (default: the one for the file's tax year)")
    sh.add_argument("--record", help="only this record type, e.g. RW")
    sh.add_argument("--limit", type=int, help="show at most this many records")
    sh.add_argument("--show-ssn", action="store_true", help="print full SSNs")
    sh.set_defaults(func=cmd_show)

    ly = sub.add_parser("layout", help="list a layout table and where each field came from")
    ly.add_argument("name", nargs="?", default="efw2_ty2025", help="layout name or path (default: efw2_ty2025)")
    ly.add_argument("--record", help="only this record type")
    ly.add_argument("--unverified", action="store_true", help="only fields not confirmed against a spec")
    ly.set_defaults(func=cmd_layout)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # output piped into head, for example
        sys.stdout = open(os.devnull, "w")
        return 0
