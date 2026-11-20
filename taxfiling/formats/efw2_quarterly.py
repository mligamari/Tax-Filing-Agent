"""Quarterly unemployment-insurance (UI) wage file in SSA's EFW2 format.

SSA's EFW2 specification (Pub 42-007) defines the RS (State) record for both yearly
income tax and quarterly UI reporting. This writer fills the UI part of RS:

    197-202  Reporting Period          last month and year of the quarter, e.g. 032025
    203-213  State Quarterly UI Total Wages
    214-224  State Quarterly UI Total Taxable Wages
    248-267  State Employer Account Number   (the employer's UI account)

Positions, widths and padding come from the same generated layout tables as the
W-2 writer, with the state's own changes from its profile ("layout_overrides": a
state-defined RT totals record, its line ending, blank instead of zero-filled empty
fields), and the RA and RE records are built by the same code. Which records go
in the file, constant values and required fields come from the state's
"ui_quarterly" profile (taxfiling/states/<state>.json), so a state whose own
specification differs is a profile change, not new code.

A profile marked "provisional" was built without the state's own specification;
the writer says so in its notes every time it runs.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..issues import Issues
from ..layout import Layout
from ..model import QuarterlyReport, QuarterlyWages
from .efw2 import Efw2Builder, render

QUARTER_END_MONTH = {1: "03", 2: "06", 3: "09", 4: "12"}


def reporting_period(year: int, quarter: int) -> str:
    """SSA RS 197-202: MMCCYY of the quarter's last month, e.g. Q1 2025 -> 032025."""
    return f"{QUARTER_END_MONTH[quarter]}{year}"


class QuarterlyEfw2Builder(Efw2Builder):
    def __init__(self, layout: Layout, profile: dict):
        super().__init__(layout, profile)
        self.record_set = profile.get("records", ["RA", "RE", "RS", "RF"])

    def _person_q(self, w: QuarterlyWages, where: str) -> dict[str, Any]:
        values = {
            "ssn": w.ssn,
            "first_name": self._fit(w.first_name, "RS", "first_name", where),
            "middle_name": self._fit(w.middle_name, "RS", "middle_name", where),
            "last_name": self._fit(w.last_name, "RS", "last_name", where),
            "suffix": self._fit(w.suffix, "RS", "suffix", where),
        }
        values.update(self._address(w.address, "RS", "", where))
        return values

    def rs_quarterly(self, w: QuarterlyWages, report: QuarterlyReport, account: str) -> None:
        where = w.where()
        values = self._person_q(w, where)
        values.update(self.profile.get("state_record_values", {}))
        values.update({
            "reporting_period": reporting_period(report.tax_year, report.quarter),
            "ui_total_wages": w.gross_wages,
            "state_employer_account": account,
        })
        if self.profile.get("employee_taxable_wages", True):
            values["ui_taxable_wages"] = w.taxable_wages
        rule = self.profile.get("name_rule")
        if rule:
            for label, name in (("first name", w.first_name), ("last name", w.last_name)):
                if not re.fullmatch(rule["pattern"], name):
                    self.issues.warning(where, f"{label} '{name}' has characters other than letters; "
                                               f"{rule['description']}")
        self._add("RS", values, where)

    def rt_vec(self, report: QuarterlyReport) -> None:
        """Employer totals in a state-defined RT record (fields named by meaning, from the profile's layout)."""
        emps = report.employees
        values: dict[str, Any] = {
            "employee_count": str(len(emps)),
            "total_ui_wages": sum((e.gross_wages for e in emps), Decimal(0)),
            "total_excess_wages": sum((e.excess_wages for e in emps), Decimal(0)),
            "total_taxable_wages": sum((e.taxable_wages for e in emps), Decimal(0)),
        }
        for m, count in enumerate(report.employed_12th_counts, 1):
            values[f"month{m}_employees"] = str(count)
        self._add("RT", {k: v for k, v in values.items() if k in self.layout["RT"]}, "totals")

    def build_quarterly(self, report: QuarterlyReport) -> list[tuple[str, dict[str, Any]]]:
        state = self.profile["state"]
        if self.profile.get("provisional"):
            self.issues.warning("profile", f"PROVISIONAL {self.profile['state_name']} UI format: "
                                           f"{self.profile.get('provisional_note', '')}")
        if self.layout.tax_year and report.tax_year != self.layout.tax_year:
            self.issues.error("input", f"tax year {report.tax_year} does not match the layout table "
                                       f"({self.layout.tax_year})")
        if report.quarter not in QUARTER_END_MONTH:
            self.issues.error("input", f"quarter {report.quarter} is not 1-4")
            return self.records
        if report.state != state:
            self.issues.error("input", f"report is for {report.state}, profile is for {state}")
        account = re.sub(r"[\s\-]", "", report.employer.ui_accounts.get(state, "")).upper()
        if not account:
            self.issues.error("employer", f"no {self.profile['state_name']} unemployment-insurance account number; "
                                          f"add it to the settings file under employer.ui_accounts.{state}")
        rule = self.profile.get("account_number")
        if account and rule and not re.fullmatch(rule["pattern"], account):
            self.issues.error("employer", f"UI account '{account}' does not match the {self.profile['state_name']} "
                                          f"format: {rule['description']}")
        if account and rule and rule.get("write") == "zero_fill":
            account = account.rjust(self.layout["RS"]["state_employer_account"].length, "0")
        if report.submitter is None:
            self.issues.error("input", "no submitter settings (they supply the RA record)")
            return self.records

        if "RA" in self.record_set:
            self.ra(report)  # type: ignore[arg-type]  (uses .submitter only)
        self.re(report)      # type: ignore[arg-type]  (uses .tax_year and .employer only)
        for w in report.employees:
            if not w.address.delivery:
                self.issues.warning(w.where(), "no address found for this employee on the W-2 sheet; "
                                               "RS address fields left blank")
            self.rs_quarterly(w, report, account)
        if not report.employees:
            self.issues.error("input", f"no employees with wages in {report.tax_year} Q{report.quarter}")
        if "RT" in self.record_set:
            self.rt_vec(report)
        if "RF" in self.record_set:
            self.rf(sum(1 for rt, _ in self.records if rt == "RW"))
        return self.records


def build_efw2_quarterly(report: QuarterlyReport, layout: Layout, profile: dict) -> tuple[str | None, Issues]:
    """Returns (file text, issues). File text is None when there are errors."""
    builder = QuarterlyEfw2Builder(layout, profile)
    records = builder.build_quarterly(report)
    if builder.issues.errors:
        return None, builder.issues
    return render(records, layout, profile.get("final_line_ending", True)), builder.issues
