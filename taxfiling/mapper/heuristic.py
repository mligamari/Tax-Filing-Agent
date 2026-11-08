"""No-model mode: propose a mapping from header names alone, with fixed rules.

It reads the same outbound profile a model would get and answers in the same format,
so the rest of the pipeline cannot tell the difference. It is deliberately cautious:
when a rule matches two columns (say two headers that both say only "State"), it maps
neither and says so in the notes, for a person or a model to decide.
"""
from __future__ import annotations

import re

ABBREVIATIONS = {
    "fed": "federal", "fit": "federal tax", "ss": "social security", "soc": "social", "sec": "security",
    "oasdi": "social security", "med": "medicare", "medi": "medicare", "mcare": "medicare",
    "wh": "withheld", "whld": "withheld", "withholding": "withheld", "wgs": "wages", "wage": "wages",
    "amt": "amount", "cd": "code", "emp": "employee", "ee": "employee", "er": "employer", "co": "company",
    "st": "state", "fein": "ein", "alloc": "allocated", "dep": "dependent", "nq": "nonqualified",
    "nonqual": "nonqualified", "ret": "retirement", "stat": "statutory", "mi": "middle", "init": "initial",
    "sfx": "suffix", "addr": "address", "zipcode": "zip", "postal": "zip", "fname": "first",
    "lname": "last", "sit": "state tax", "acct": "account", "no": "number", "num": "number", "nbr": "number",
    "3p": "third party", "3rd": "third",
}

EMPLOYER = r"\b(employer|company)\b"
STATE_AMOUNT = r"\b(wages|tax|withheld|id|ein|account|number|income|15|16|17)\b"

# field -> (include pattern, exclude pattern). Tried in this order, each column used once,
# so the specific fields come before the general ones.
RULES: list[tuple[str, str, str | None]] = [
    ("employer_ein", r"\bein\b|\bemployer (id|identification)\b|\bfederal (id|tax id)\b", r"\bstate\b"),
    ("employer_name", EMPLOYER + r" name\b|^(employer|company)( c)?$", None),
    ("employer_address", EMPLOYER + r" .*\b(address|street)\b", None),
    ("employer_city", EMPLOYER + r" .*\bcity\b", None),
    ("employer_zip", EMPLOYER + r" .*\bzip\b", None),
    ("employer_state", EMPLOYER + r" .*\bstate\b", STATE_AMOUNT),
    ("box15_state_id", r"\bstate\b.*\b(id|ein|account|number)\b|\b15\b.*\bid\b", r"\bsocial security\b"),
    ("box16_state_wages", r"\bbox 16\b|\bstate (taxable )?wages\b", None),
    ("box17_state_tax", r"\bbox 17\b|\bstate (income )?tax\b|\bstate withheld\b", None),
    ("box15_state", r"\bbox 15 state\b", None),
    ("ssn", r"\bssn\b|\bsocial security number\b|\bsocial security #|\bsocial #", None),
    ("control_number", r"\bcontrol\b|\bemployee (id|number)\b|\brecord id\b", None),
    ("first_name", r"\bfirst\b", EMPLOYER),
    ("middle_name", r"\bmiddle\b", EMPLOYER),
    ("last_name", r"\blast\b|\bsurname\b", EMPLOYER),
    ("suffix", r"\bsuffix\b", EMPLOYER),
    ("address_line_2", r"\b(address|street)\b.*\b(2|two|apt|suite|unit)\b|\b(apt|suite|unit)\b", EMPLOYER),
    ("address", r"\b(address|street)\b", EMPLOYER),
    ("city", r"\bcity\b", EMPLOYER),
    ("zip", r"\bzip\b", EMPLOYER),
    ("state", r"\bstate\b", EMPLOYER + "|" + STATE_AMOUNT),
    ("box1_wages", r"\bbox 1\b|\bfederal (taxable )?wages\b|\bwages tips other\b", None),
    ("box2_fed_tax", r"\bbox 2\b|\bfederal (income )?(tax|withheld)\b", None),
    ("box3_ss_wages", r"\bbox 3\b|\bsocial security wages\b", None),
    ("box4_ss_tax", r"\bbox 4\b|\bsocial security (tax|withheld)\b", None),
    ("box5_medicare_wages", r"\bbox 5\b|\bmedicare wages\b", None),
    ("box6_medicare_tax", r"\bbox 6\b|\bmedicare (tax|withheld)\b", None),
    ("box7_ss_tips", r"\bbox 7\b|\bsocial security tips\b", None),
    ("box8_allocated_tips", r"\bbox 8\b|\ballocated tips\b", None),
    ("box10_dependent_care", r"\bbox 10\b|\bdependent care\b", None),
    ("box11_nonqualified", r"\bbox 11\b|\bnonqualified\b", None),
    ("statutory_employee", r"\bstatutory\b", None),
    ("retirement_plan", r"\bretirement\b", None),
    ("third_party_sick_pay", r"\bthird party\b|\bsick pay\b", None),
    ("box14b_tipped_occupation", r"\b14b\b|\btipped occupation\b", None),
]

BOX12_CODE = re.compile(r"\b12 ?([a-h])\b.*\bcode\b|\bcode\b.*\b12 ?([a-h])\b")
BOX12_AMOUNT = re.compile(r"\b12 ?([a-h])\b.*\bamount\b|\bamount\b.*\b12 ?([a-h])\b")


def normalize(header: str) -> str:
    text = header.lower().replace("w/h", " withheld ").replace("w-h", " withheld ").replace("&", " and ")
    text = re.sub(r"third[- ]party|\b3rd[- ]party", "third party", text)
    text = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", text)
    text = text.replace("#", " # ")
    tokens = re.findall(r"[a-z0-9]+|#", text)
    out = " ".join(ABBREVIATIONS.get(t, t) for t in tokens)
    return re.sub(r"\bsocial security (number|#)", "social security number", out)


def _sheet_proposal(sheet: dict) -> dict:
    columns = [(c["id"], normalize(c["header"])) for c in sheet["columns"] if c.get("header")]
    used: set[str] = set()
    mapped, notes = [], []
    for field, include, exclude in RULES:
        hits = [cid for cid, h in columns if cid not in used and re.search(include, h)
                and not (exclude and re.search(exclude, h))]
        if len(hits) == 1:
            used.add(hits[0])
            mapped.append({"field": field, "column": hits[0], "confidence": "medium",
                           "reason": "header matches the rule for this field"})
        elif len(hits) > 1:
            notes.append(f"{field}: columns {', '.join(hits)} all match; left unmapped for you to choose")
    codes, amounts = {}, {}
    for cid, h in columns:
        if cid in used:
            continue
        if (m := BOX12_CODE.search(h)):
            codes[m.group(1) or m.group(2)] = cid
        elif (m := BOX12_AMOUNT.search(h)):
            amounts[m.group(1) or m.group(2)] = cid
    box12 = [{"code_column": codes[s], "amount_column": amounts[s]} for s in sorted(codes) if s in amounts]
    return {"sheet": sheet["id"], "columns": mapped, "box12": box12, "notes": notes}


def propose_offline(workbook: dict) -> dict:
    """The best sheet's proposal: the sheet where the rules map the most fields."""
    proposals = [_sheet_proposal(s) for s in workbook["sheets"] if s.get("columns")]
    if not proposals:
        return {"sheet": workbook["sheets"][0]["id"] if workbook["sheets"] else "", "columns": [], "box12": [],
                "notes": ["no sheet with a header row was found"]}
    best = max(proposals, key=lambda p: len(p["columns"]) + len(p["box12"]))
    best["notes"].insert(0, "proposed by fixed header rules (no model)")
    return best
