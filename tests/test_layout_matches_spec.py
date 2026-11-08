"""Every field of every layout table is checked against the text of SSA's own specification.

specs/ssa/efw2_ty<YEAR>.txt is the text of SSA's PDF for that tax year. These tests read
the record tables out of it independently of the layout file and compare field by field,
so a typo in a position, a missing field or a field in the wrong record cannot go unnoticed.
"""
import hashlib
import sys
from pathlib import Path

import pytest

from taxfiling.layout import load_layout

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from efw2_spec_text import load_spec, parse_box12_chart, problems, same_name  # noqa: E402

YEARS = [2025, 2026]


def spec_path(year):
    return ROOT / "specs" / "ssa" / f"efw2_ty{year}.txt"


@pytest.mark.parametrize("year", YEARS)
def test_the_spec_text_reads_cleanly(year):
    spec = load_spec(spec_path(year))
    assert set(spec) == {"RA", "RE", "RW", "RO", "RS", "RT", "RU", "RV", "RF"}
    assert problems(spec) == []  # every record contiguous to 512, and its summary table agrees


@pytest.mark.parametrize("year", YEARS)
def test_every_field_matches_the_spec(year):
    layout = load_layout(f"efw2_ty{year}")
    spec = load_spec(spec_path(year))
    assert set(layout.records) == set(spec)
    for rt, rec in layout.records.items():
        theirs = [(f.start, f.end) for f in spec[rt].fields]
        ours = [(f.start, f.end) for f in rec.fields]
        assert ours == theirs, rt
        for f, s in zip(rec.fields, spec[rt].fields):
            where = f"{rt} {f.start}-{f.end}"
            assert same_name(f.name, s.name), where
            assert f.page == s.page, where
            assert f.sources == (f"ssa-ty{year}",), where
            if f.type == "blank":
                assert s.name in ("Blank", "Blanks"), where
            if f.type == "const":
                assert f'Constant "{f.value}"' in s.spec, where
            if s.required and not (rt == "RE" and f.key == "tax_jurisdiction"):
                assert f.required, where


@pytest.mark.parametrize("year", YEARS)
def test_box_12_codes_go_where_appendix_e_says(year):
    layout = load_layout(f"efw2_ty{year}")
    chart = parse_box12_chart(spec_path(year).read_text(encoding="utf-8"))
    assert set(layout.box12_codes) == set(chart)
    for code, c in chart.items():
        listed = layout.box12_codes[code]
        if c.record is None:
            assert listed["key"] is None, code
            continue
        f = layout[c.record][listed["key"]]
        assert (c.record, f.start, f.end) == (listed["record"], c.start, c.end), code
        assert code in f.key[5:].split("_"), code


def test_what_changed_between_2025_and_2026():
    """The differences SSA lists in the 2026 "What's New", and nothing else."""
    a, b = load_layout("efw2_ty2025"), load_layout("efw2_ty2026")
    changed = {}
    for rt in a.records:
        old = {(f.start, f.end, f.key) for f in a[rt].fields}
        new = {(f.start, f.end, f.key) for f in b[rt].fields}
        if old != new:
            changed[rt] = sorted(k for _, _, k in new - old)
    assert set(changed) == {"RE", "RW", "RO", "RU"}
    assert changed["RE"] == ["blank_329", "employer_signature_pin"]
    assert changed["RW"] == ["blank_496", "tipped_occupation_primary", "tipped_occupation_secondary"]
    assert {"code_A", "code_B", "code_J", "code_K", "code_L", "code_P", "code_TA", "code_TP", "code_TT"} \
        <= set(changed["RO"])


@pytest.mark.parametrize("year", YEARS)
def test_the_layout_names_the_exact_spec_text_it_was_built_from(year):
    layout = load_layout(f"efw2_ty{year}")
    source = layout.sources[f"ssa-ty{year}"]
    assert source["sha256"] == hashlib.sha256(spec_path(year).read_bytes()).hexdigest()
    assert source["url"].startswith("https://www.ssa.gov/employer/efw/")
