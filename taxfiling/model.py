"""Format-neutral data model for one employer's wage report.

Readers (spreadsheet, CSV, later the AI column mapper) produce these objects;
format writers (EFW2, later ICESA) consume them. Amounts are Decimal dollars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Address:
    delivery: str = ""          # street address
    location: str = ""          # suite, apartment, attention line
    city: str = ""
    state: str = ""             # postal abbreviation
    zip: str = ""
    zip_ext: str = ""


@dataclass
class Contact:
    name: str = ""
    phone: str = ""
    phone_ext: str = ""
    email: str = ""
    fax: str = ""


@dataclass
class Submitter:
    """Who transmits the file (EFW2 RA record). Not on a W-2, so it comes from a config file."""
    ein: str
    user_id: str                # SSA BSO User ID (8 characters)
    name: str
    address: Address
    contact: Contact
    software_code: str = "98"   # 98 = in-house program
    preparer_code: str = "L"    # L = self-prepared
    resub: bool = False
    resub_wfid: str = ""


@dataclass
class Employer:
    ein: str
    name: str
    address: Address
    contact: Contact = field(default_factory=Contact)
    kind_of_employer: str = "N"     # N = none of F/S/T/Y apply (private for-profit)
    employment_code: str = "R"      # R = regular (Form 941)
    terminating_business: bool = False
    state_ids: dict[str, str] = field(default_factory=dict)  # state postal code -> state employer account


@dataclass
class StateWages:
    state: str                  # Box 15 postal code
    employer_state_id: str      # Box 15 state ID
    wages: Decimal              # Box 16
    tax: Decimal                # Box 17


@dataclass
class W2:
    """One employee's Form W-2. box12 maps code (e.g. "D", "DD") to amount."""
    control_number: str
    ssn: str                    # 9 digits, no hyphens
    first_name: str
    middle_name: str
    last_name: str
    suffix: str
    address: Address
    box1_wages: Decimal = Decimal(0)
    box2_fed_tax: Decimal = Decimal(0)
    box3_ss_wages: Decimal = Decimal(0)
    box4_ss_tax: Decimal = Decimal(0)
    box5_medicare_wages: Decimal = Decimal(0)
    box6_medicare_tax: Decimal = Decimal(0)
    box7_ss_tips: Decimal = Decimal(0)
    box8_allocated_tips: Decimal = Decimal(0)
    box10_dependent_care: Decimal = Decimal(0)
    box11_nonqualified: Decimal = Decimal(0)
    box12: dict[str, Decimal] = field(default_factory=dict)
    statutory_employee: bool = False
    retirement_plan: bool = False
    third_party_sick_pay: bool = False
    box14b_tipped_occupation: str = ""
    states: list[StateWages] = field(default_factory=list)
    source_row: int | None = None   # spreadsheet row, for error messages

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def where(self) -> str:
        row = f"row {self.source_row}, " if self.source_row else ""
        return f"{row}control {self.control_number} ({self.display_name})"


@dataclass
class WageReport:
    tax_year: int
    submitter: Submitter | None
    employer: Employer
    employees: list[W2]
