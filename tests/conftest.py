from pathlib import Path

import pytest

from taxfiling.config import apply_employer_settings, load_submitter
from taxfiling.layout import load_layout
from taxfiling.model import WageReport
from taxfiling.readers.excel import load_mapping, read_w2_workbook
from taxfiling.states import report_profile

ROOT = Path(__file__).resolve().parent.parent
VA_EXAMPLE = ROOT / "examples" / "va_2026"
VA_WORKBOOK = VA_EXAMPLE / "w2_2026_va_synthetic.xlsx"
VA_SUBMITTER = VA_EXAMPLE / "submitter.json"
VA_GOLDEN = VA_EXAMPLE / "output" / "W2 REPORT 2026.txt"


@pytest.fixture(scope="session")
def layout():
    return load_layout("efw2_ty2026")


@pytest.fixture(scope="session")
def va_profile():
    return report_profile("VA", "w2")


@pytest.fixture
def va_report():
    """A fresh WageReport for the synthetic Virginia employer (tests may modify it)."""
    employer, employees, issues = read_w2_workbook(VA_WORKBOOK, load_mapping("w2_standard"))
    assert not issues.errors
    submitter, settings = load_submitter(VA_SUBMITTER)
    apply_employer_settings(employer, settings, submitter)
    return WageReport(tax_year=2026, submitter=submitter, employer=employer, employees=employees)
