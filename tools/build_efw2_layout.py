"""Build taxfiling/layouts/efw2_ty<YEAR>.json from SSA's EFW2 specification text.

Positions, lengths, field names, page numbers and SSA's "This is a required
field" flags are read from specs/ssa/efw2_ty<YEAR>.txt (the text of SSA's PDF),
not typed in. What is typed in below is only what the program needs on top of
the spec: a short key for each field, its kind (alphanumeric, numeric, money,
blank), and for totals which field they add up.

The script refuses to write a layout when:
  * a field in the spec has no key below, or a key below has no field in the spec;
  * a Box 12 key (code_D, code_A_B ...) is on a field whose SSA name does not name that code,
    or disagrees with Appendix E's chart of where each Box 12 code goes;
  * a total is not named "Total " + the name of the field it adds up;
  * a kind contradicts the spec text (a "Constant" that is not const, a "Fill with
    blanks" field that is not blank, a "Right justify and zero fill" field that is
    alphanumeric).

Run: python tools/build_efw2_layout.py 2025 taxfiling/layouts/efw2_ty2025.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from efw2_spec_text import load_spec, parse_box12_chart, problems, same_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SPECS = {
    2025: {"title": "SSA Pub. 42-007, Specifications for Filing Forms W-2 Electronically (EFW2), Tax Year 2025, "
                    "Version 3.3 (01/20/2026)",
           "url": "https://www.ssa.gov/employer/efw/25efw2.pdf"},
    2026: {"title": "SSA Pub. 42-007, Specifications for Filing Forms W-2 Electronically (EFW2), Tax Year 2026, "
                    "Version 2 (9/14/2026)",
           "url": "https://www.ssa.gov/employer/efw/26efw2.pdf"},
}

RECORD_NAMES = {"RA": "Submitter", "RE": "Employer", "RW": "Employee", "RO": "Employee optional",
                "RS": "State", "RT": "Total (sums of RW)", "RU": "Total optional (sums of RO)",
                "RV": "State total", "RF": "Final"}

ALL = (2025, 2026)


def K(start, end, key, typ, years=ALL, **extra):
    return {"start": start, "end": end, "key": key, "type": typ, "years": years, **extra}


def blank(start, end, years=ALL):
    return K(start, end, f"blank_{start}", "blank", years)


def money(start, key, width=11, years=ALL, **extra):
    return K(start, start + width - 1, key, "M", years, **extra)


def total(start, sum_of, years=ALL, key=None, **extra):
    return K(start, start + 14, key or f"total_{sum_of}", "M", years, sum_of=sum_of, **extra)


ADDRESS = {  # name/address block shared by RA (twice), RE, RW and RS: offsets from its location address
    "location_address": (0, 21, "A"), "delivery_address": (22, 43, "A"), "city": (44, 65, "A"),
    "state": (66, 67, "A"), "zip": (68, 72, "A"), "zip_ext": (73, 76, "A"),
}


def address(first: int, prefix: str) -> list[dict]:
    return [K(first + a, first + b, prefix + name, typ) for name, (a, b, typ) in ADDRESS.items()]


def foreign(first: int, prefix: str) -> list[dict]:
    """Blank (5), foreign state/province (23), foreign postal code (15), country code (2)."""
    return [blank(first, first + 4),
            K(first + 5, first + 27, prefix + "foreign_state", "A"),
            K(first + 28, first + 42, prefix + "foreign_postal", "A"),
            K(first + 43, first + 44, prefix + "country", "A")]


KEYS = {
    "RA": [
        K(1, 2, "record_id", "const"),
        K(3, 11, "submitter_ein", "N"),
        K(12, 19, "user_id", "A"),
        K(20, 23, "software_vendor_code", "A"),
        blank(24, 28),
        K(29, 29, "resub_indicator", "N"),
        K(30, 35, "resub_wfid", "A"),
        K(36, 37, "software_code", "N"),
        K(38, 94, "company_name", "A"),
        *address(95, "company_"),
        *foreign(172, "company_"),
        K(217, 273, "submitter_name", "A"),
        *address(274, "submitter_"),
        *foreign(351, "submitter_"),
        K(396, 422, "contact_name", "A"),
        K(423, 437, "contact_phone", "A"),
        K(438, 442, "contact_phone_ext", "A"),
        blank(443, 445),
        K(446, 485, "contact_email", "A", case="mixed"),
        blank(486, 488),
        K(489, 498, "contact_fax", "A"),
        blank(499, 499),
        K(500, 500, "preparer_code", "A"),
        blank(501, 512),
    ],
    "RE": [
        K(1, 2, "record_id", "const"),
        K(3, 6, "tax_year", "N"),
        K(7, 7, "agent_indicator", "A"),
        K(8, 16, "employer_ein", "N"),
        K(17, 25, "agent_for_ein", "A"),
        K(26, 26, "terminating_business", "N"),
        K(27, 30, "establishment_number", "A"),
        K(31, 39, "other_ein", "A"),
        K(40, 96, "employer_name", "A"),
        *address(97, "employer_"),
        K(174, 174, "kind_of_employer", "A"),
        blank(175, 178),
        K(179, 201, "employer_foreign_state", "A"),
        K(202, 216, "employer_foreign_postal", "A"),
        K(217, 218, "employer_country", "A"),
        K(219, 219, "employment_code", "A"),
        K(220, 220, "tax_jurisdiction", "A", required=False,
          notes="SSA marks this field required, but its code for a W-2 is a blank"),
        K(221, 221, "third_party_sick_pay", "N"),
        K(222, 248, "employer_contact_name", "A"),
        K(249, 263, "employer_contact_phone", "A"),
        K(264, 268, "employer_contact_phone_ext", "A"),
        K(269, 278, "employer_contact_fax", "A"),
        K(279, 318, "employer_contact_email", "A", case="mixed"),
        blank(319, 512, years=(2025,)),
        K(319, 328, "employer_signature_pin", "A", years=(2026,), notes="Format S-XXXXXX-Y; optional"),
        blank(329, 512, years=(2026,)),
    ],
    "RW": [
        K(1, 2, "record_id", "const"),
        K(3, 11, "ssn", "N"),
        K(12, 26, "first_name", "A"),
        K(27, 41, "middle_name", "A"),
        K(42, 61, "last_name", "A"),
        K(62, 65, "suffix", "A"),
        *address(66, ""),
        *foreign(143, ""),
        money(188, "box1_wages"),
        money(199, "box2_fed_tax"),
        money(210, "box3_ss_wages"),
        money(221, "box4_ss_tax"),
        money(232, "box5_medicare_wages"),
        money(243, "box6_medicare_tax"),
        money(254, "box7_ss_tips"),
        blank(265, 275),
        money(276, "box10_dependent_care"),
        money(287, "code_D"),
        money(298, "code_E"),
        money(309, "code_F"),
        money(320, "code_G"),
        money(331, "code_H"),
        blank(342, 352),
        money(353, "box11_nonqual_457"),
        money(364, "code_W"),
        money(375, "box11_nonqual_not_457"),
        money(386, "code_Q"),
        blank(397, 407),
        money(408, "code_C"),
        money(419, "code_V"),
        money(430, "code_Y"),
        money(441, "code_AA"),
        money(452, "code_BB"),
        money(463, "code_DD"),
        money(474, "code_FF"),
        blank(485, 485),
        K(486, 486, "statutory_employee", "N"),
        blank(487, 487),
        K(488, 488, "retirement_plan", "N"),
        K(489, 489, "third_party_sick_pay", "N"),
        blank(490, 512, years=(2025,)),
        K(490, 492, "tipped_occupation_primary", "N", years=(2026,), empty="blank",
          notes="Box 14b; three-digit Treasury tipped occupation code when Box 12 code TP is present"),
        K(493, 495, "tipped_occupation_secondary", "N", years=(2026,), empty="blank",
          notes="Box 14b; only when there is more than one occupation code"),
        blank(496, 512, years=(2026,)),
    ],
    "RO": [
        K(1, 2, "record_id", "const"),
        blank(3, 11),
        money(12, "box8_allocated_tips"),
        money(23, "code_A_B", years=(2025,), notes="Codes A and B added together"),
        money(23, "tips_tax_a_b_until_2025", years=(2026,),
              notes="Valid through tax year 2025 only; zero for 2026, when codes A and B have their own fields"),
        money(34, "code_R"),
        money(45, "code_S"),
        money(56, "code_T"),
        money(67, "code_M"),
        money(78, "code_N"),
        money(89, "code_Z"),
        blank(100, 110),
        money(111, "code_EE"),
        money(122, "code_GG"),
        money(133, "code_HH"),
        money(144, "code_II"),
        blank(155, 274, years=(2025,)),
        money(155, "code_A", years=(2026,)),
        money(166, "code_B", years=(2026,)),
        money(177, "code_J", years=(2026,)),
        money(188, "code_K", years=(2026,)),
        money(199, "code_L", years=(2026,)),
        money(210, "code_P", years=(2026,)),
        money(221, "code_TA", years=(2026,)),
        money(232, "code_TP", years=(2026,)),
        money(243, "code_TT", years=(2026,)),
        blank(254, 274, years=(2026,)),
        money(275, "pr_wages"),
        money(286, "pr_commissions"),
        money(297, "pr_allowances"),
        money(308, "pr_tips"),
        money(319, "pr_total"),
        money(330, "pr_tax"),
        money(341, "pr_retirement_fund"),
        blank(352, 362),
        money(363, "territory_wages"),
        money(374, "territory_tax"),
        blank(385, 512),
    ],
    "RS": [
        K(1, 2, "record_id", "const"),
        K(3, 4, "state_code", "N", notes="Postal numeric code (Appendix F)"),
        K(5, 9, "taxing_entity_code", "A"),
        K(10, 18, "ssn", "N"),
        K(19, 33, "first_name", "A"),
        K(34, 48, "middle_name", "A"),
        K(49, 68, "last_name", "A"),
        K(69, 72, "suffix", "A"),
        *address(73, ""),
        *foreign(150, ""),
        K(195, 196, "optional_code", "A"),
        K(197, 202, "reporting_period", "N", empty="blank", notes="MMCCYY; unemployment reporting"),
        money(203, "ui_total_wages"),
        money(214, "ui_taxable_wages"),
        K(225, 226, "weeks_worked", "N", empty="blank"),
        K(227, 234, "date_first_employed", "N", empty="blank", notes="MMDDCCYY"),
        K(235, 242, "date_of_separation", "N", empty="blank", notes="MMDDCCYY"),
        blank(243, 247),
        K(248, 267, "state_employer_account", "A"),
        blank(268, 273),
        K(274, 275, "state_code_2", "N", notes="Postal numeric code (Appendix F)"),
        money(276, "state_wages"),
        money(287, "state_tax"),
        K(298, 307, "other_state_data", "A"),
        K(308, 308, "tax_type_code", "A", notes="C city, D county, E school district, F other"),
        money(309, "local_wages"),
        money(320, "local_tax"),
        K(331, 337, "state_control_number", "A"),
        K(338, 412, "supplemental_1", "A"),
        K(413, 487, "supplemental_2", "A"),
        blank(488, 512),
    ],
    "RT": [
        K(1, 2, "record_id", "const"),
        K(3, 9, "rw_count", "N", count_of="RW"),
        total(10, "box1_wages"),
        total(25, "box2_fed_tax"),
        total(40, "box3_ss_wages"),
        total(55, "box4_ss_tax"),
        total(70, "box5_medicare_wages"),
        total(85, "box6_medicare_tax"),
        total(100, "box7_ss_tips"),
        blank(115, 129),
        total(130, "box10_dependent_care"),
        total(145, "code_D"),
        total(160, "code_E"),
        total(175, "code_F"),
        total(190, "code_G"),
        total(205, "code_H"),
        blank(220, 234),
        total(235, "box11_nonqual_457"),
        total(250, "code_W"),
        total(265, "box11_nonqual_not_457"),
        total(280, "code_Q"),
        total(295, "code_DD"),
        total(310, "code_C"),
        total(325, None, key="total_third_party_withheld",
              notes="Not a sum of RW fields: tax withheld by a third-party sick pay payer"),
        total(340, "code_V"),
        total(355, "code_Y"),
        total(370, "code_AA"),
        total(385, "code_BB"),
        total(400, "code_FF"),
        blank(415, 512),
    ],
    "RU": [
        K(1, 2, "record_id", "const"),
        K(3, 9, "ro_count", "N", count_of="RO"),
        total(10, "box8_allocated_tips"),
        total(25, "code_A_B", years=(2025,)),
        total(25, "tips_tax_a_b_until_2025", years=(2026,)),
        total(40, "code_R"),
        total(55, "code_S"),
        total(70, "code_T"),
        total(85, "code_M"),
        total(100, "code_N"),
        total(115, "code_Z"),
        blank(130, 144),
        total(145, "code_EE"),
        total(160, "code_GG"),
        total(175, "code_HH"),
        total(190, "code_II"),
        blank(205, 354, years=(2025,)),
        total(205, "code_A", years=(2026,)),
        total(220, "code_B", years=(2026,)),
        total(235, "code_J", years=(2026,)),
        total(250, "code_K", years=(2026,)),
        total(265, "code_L", years=(2026,)),
        total(280, "code_P", years=(2026,)),
        total(295, "code_TA", years=(2026,)),
        total(310, "code_TP", years=(2026,)),
        total(325, "code_TT", years=(2026,)),
        blank(340, 354, years=(2026,)),
        total(355, "pr_wages"),
        total(370, "pr_commissions"),
        total(385, "pr_allowances"),
        total(400, "pr_tips"),
        total(415, "pr_total"),
        total(430, "pr_tax"),
        total(445, "pr_retirement_fund"),
        total(460, "territory_wages"),
        total(475, "territory_tax"),
        blank(490, 512),
    ],
    "RV": [
        K(1, 2, "record_id", "const"),
        K(3, 512, "supplemental_data", "A", notes="Defined by the state"),
    ],
    "RF": [
        K(1, 2, "record_id", "const"),
        blank(3, 7),
        K(8, 16, "rw_count", "N", count_of="RW"),
        blank(17, 512),
    ],
}

TOTALS_OF = {"RT": "RW", "RU": "RO"}


def codes_in_key(key: str) -> list[str]:
    """code_D -> ["D"]; code_A_B -> ["A", "B"] (Box 12 codes never contain an underscore)."""
    return key[5:].split("_") if key.startswith("code_") else []


def build(year: int) -> dict:
    text_path = ROOT / "specs" / "ssa" / f"efw2_ty{year}.txt"
    spec = load_spec(text_path)
    errors = [f"spec text: {p}" for p in problems(spec)]
    source_id = f"ssa-ty{year}"
    records = {}
    for rt, entries in KEYS.items():
        mine = [e for e in entries if year in e["years"]]
        by_span = {(e["start"], e["end"]): e for e in mine}
        if len(by_span) != len(mine):
            errors.append(f"{rt}: two keys for the same positions")
        if rt not in spec:
            errors.append(f"{rt}: not in the {year} spec")
            continue
        fields = []
        for sf in spec[rt].fields:
            e = by_span.pop((sf.start, sf.end), None)
            where = f"{rt} {sf.start}-{sf.end} ({sf.name})"
            if e is None:
                errors.append(f"{where}: in the spec but has no key in this script")
                continue
            spec_text = sf.spec
            const = re.match(r'Constant "([A-Z]{2})"', spec_text)
            says_blank = (spec_text.startswith(("Fill with blank", "Fill with a blank"))
                          or (sf.name in ("Blank", "Blanks") and not spec_text))  # 2026 RE 329-512 has no text
            if (e["type"] == "const") != bool(const):
                errors.append(f"{where}: spec says {spec_text[:40]!r}, key is {e['type']}")
            if (e["type"] == "blank") != says_blank:
                errors.append(f"{where}: spec says {spec_text[:40]!r}, key is {e['type']}")
            if e["type"] == "A" and "right justify and zero fill" in spec_text.lower():
                errors.append(f"{where}: spec says right justify and zero fill, key is alphanumeric")
            if e["type"] in ("N", "M") and "left justify" in spec_text.lower():
                errors.append(f"{where}: spec says left justify, key is {e['type']}")
            codes = codes_in_key(e["key"])
            if codes:
                named = re.findall(r"\(Codes? ([A-Z]{1,2})(?: and ([A-Z]{1,2}))?\s*\)", sf.name)
                found = [c for pair in named for c in pair if c]
                if found != codes:
                    errors.append(f"{where}: key {e['key']} but the spec name names codes {found}")
            required = sf.required if "required" not in e else e["required"]
            f = {"key": e["key"], "name": sf.name, "start": sf.start, "end": sf.end, "type": e["type"],
                 "required": "Y" if required else "N"}
            if const:
                f["value"] = const.group(1)
                if f["value"] != rt:
                    errors.append(f"{where}: constant {f['value']} in record {rt}")
            for opt in ("case", "empty", "sum_of", "count_of", "notes"):
                if e.get(opt) is not None:
                    f[opt] = e[opt]
            f["sources"] = [source_id]
            f["page"] = sf.page
            fields.append(f)
        for (start, end), e in by_span.items():
            errors.append(f"{rt} {start}-{end} key {e['key']}: no such field in the {year} spec")
        records[rt] = {"name": RECORD_NAMES[rt], "fields": fields}

    for rt, base in TOTALS_OF.items():
        base_fields = {f["key"]: f for f in records.get(base, {}).get("fields", [])}
        for f in records.get(rt, {}).get("fields", []):
            if f.get("sum_of") is None:
                continue
            src = base_fields.get(f["sum_of"])
            if src is None or src["type"] != "M":
                errors.append(f"{rt}.{f['key']}: sums {f['sum_of']}, which is not a money field in {base}")
            elif not (same_name(f["name"], "Total " + src["name"])  # RO names that start with "Total"
                      or src["name"].startswith("Total ") and same_name(f["name"], src["name"])):
                errors.append(f"{rt}.{f['key']}: named {f['name']!r}, but it sums {base} {src['name']!r}")

    # Appendix E's chart says where each Box 12 code goes. It must agree with the code_ keys above.
    raw = text_path.read_bytes()
    chart = parse_box12_chart(raw.decode("utf-8"))
    if not chart:
        errors.append("Appendix E Box 12 chart not found in the spec text")
    keyed = {}
    for rt in ("RW", "RO"):
        for f in records.get(rt, {}).get("fields", []):
            for code in codes_in_key(f["key"]):
                keyed[code] = (rt, f)
    box12_codes = {}
    for code, c in chart.items():
        has = keyed.pop(code, None)
        if c.record is None:
            if has:
                errors.append(f"Box 12 code {code}: Appendix E says it has no EFW2 field, but {has[0]}.{has[1]['key']} "
                              "carries it")
            box12_codes[code] = {"description": c.description, "record": None, "key": None}
        elif has is None or (has[0], has[1]["start"], has[1]["end"]) != (c.record, c.start, c.end):
            errors.append(f"Box 12 code {code}: Appendix E puts it in {c.record} {c.start}-{c.end}, keys say "
                          f"{has[0] + ' ' + str(has[1]['start']) if has else 'nowhere'}")
        else:
            box12_codes[code] = {"description": c.description, "record": c.record, "key": has[1]["key"]}
    for code, (rt, f) in keyed.items():
        errors.append(f"{rt}.{f['key']}: Box 12 code {code} is not in Appendix E")

    if errors:
        raise SystemExit("Layout not written:\n  " + "\n  ".join(errors))
    return {
        "format": "EFW2",
        "title": f"SSA EFW2, tax year {year} (W-2 wage file)",
        "tax_year": year,
        "record_length": 512,
        "line_ending": "\r\n",
        "sources": {
            source_id: {
                **SPECS[year],
                "text": f"specs/ssa/efw2_ty{year}.txt",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "how": "Positions, lengths, names, page numbers and required flags are read from the text of "
                       "SSA's PDF by tools/build_efw2_layout.py; tests/test_layout_matches_spec.py checks them "
                       "again on every test run.",
            }
        },
        "box12_codes": box12_codes,
        "records": records,
    }


def write(layout: dict, out: Path) -> None:
    lines = ["{"]
    for k, v in layout.items():
        if k == "records":
            continue
        if k == "box12_codes":
            lines.append(f"  {json.dumps(k)}: {{")
            items = list(v.items())
            lines.extend(f"    {json.dumps(c)}: {json.dumps(d)}" + ("," if i < len(items) - 1 else "")
                         for i, (c, d) in enumerate(items))
            lines.append("  },")
            continue
        dumped = json.dumps(v, indent=2).replace("\n", "\n  ") if k == "sources" else json.dumps(v)
        lines.append(f"  {json.dumps(k)}: {dumped},")
    lines.append('  "records": {')
    recs = list(layout["records"].items())
    for i, (rt, rec) in enumerate(recs):
        lines.append(f'    {json.dumps(rt)}: {{\n      "name": {json.dumps(rec["name"])},\n      "fields": [')
        fs = rec["fields"]
        lines.extend("        " + json.dumps(f) + ("," if j < len(fs) - 1 else "") for j, f in enumerate(fs))
        lines.append("      ]\n    }" + ("," if i < len(recs) - 1 else ""))
    lines.append("  }\n}")
    out.write_text("\n".join(lines) + "\n")
    json.loads(out.read_text())


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[0].isdigit() or int(argv[0]) not in SPECS:
        print(__doc__)
        return 2
    layout = build(int(argv[0]))
    write(layout, Path(argv[1]))
    for rt, rec in layout["records"].items():
        print(f"{rt}: {len(rec['fields'])} fields")
    print("wrote", argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
