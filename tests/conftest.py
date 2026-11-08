from pathlib import Path

import pytest

from taxfiling.config import apply_employer_settings, load_submitter
from taxfiling.layout import load_layout
from taxfiling.model import WageReport
from taxfiling.readers.excel import load_mapping, read_w2_workbook
from taxfiling.states import report_profile

ROOT = Path(__file__).resolve().parent.parent


def example(year: int) -> tuple[Path, Path, Path]:
    """(workbook, submitter settings, golden output) of the synthetic Virginia example for a tax year."""
    folder = ROOT / "examples" / f"va_{year}"
    return folder / f"w2_{year}_va_synthetic.xlsx", folder / "submitter.json", folder / "output" / f"W2 REPORT {year}.txt"


# Tax year 2025 is the default: its SSA specification and Virginia's guide are final.
VA_WORKBOOK, VA_SUBMITTER, VA_GOLDEN = example(2025)


def make_report(year: int) -> WageReport:
    workbook, submitter_file, _ = example(year)
    employer, employees, issues = read_w2_workbook(workbook, load_mapping("w2_standard"))
    assert not issues.errors
    submitter, settings = load_submitter(submitter_file)
    apply_employer_settings(employer, settings, submitter)
    return WageReport(tax_year=year, submitter=submitter, employer=employer, employees=employees)


@pytest.fixture(scope="session")
def layout():
    return load_layout("efw2_ty2025")


@pytest.fixture(scope="session")
def va_profile():
    return report_profile("VA", "w2", 2025)


@pytest.fixture
def va_report():
    """A fresh 2025 WageReport for the synthetic Virginia employer (tests may modify it)."""
    return make_report(2025)


@pytest.fixture(scope="session")
def layout_2026():
    return load_layout("efw2_ty2026")


@pytest.fixture(scope="session")
def va_profile_2026():
    return report_profile("VA", "w2", 2026)


@pytest.fixture
def va_report_2026():
    return make_report(2026)
