"""The W-2 fields a spreadsheet column can be mapped to.

This catalog is sent to the model as part of the prompt, so it holds only
descriptions of the fields, never data. The keys are the ones the Excel reader
understands (see taxfiling/mappings/w2_standard.json).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    description: str
    kind: str          # what the column's cells should look like: id, ssn, ein, zip, name, text, state, money, flag
    required: bool


FIELDS: tuple[Field, ...] = (
    Field("control_number", "Box d, control number (an employee or record ID)", "id", False),
    Field("ssn", "Box a, employee Social Security number", "ssn", True),
    Field("employer_ein", "Box b, employer identification number (EIN)", "ein", True),
    Field("employer_name", "Box c, employer name", "text", True),
    Field("employer_address", "Box c, employer street address", "text", True),
    Field("employer_city", "Box c, employer city", "text", True),
    Field("employer_state", "Box c, employer state (postal abbreviation)", "state", True),
    Field("employer_zip", "Box c, employer ZIP code", "zip", True),
    Field("first_name", "Box e, employee first name", "name", True),
    Field("middle_name", "Box e, employee middle name or initial", "name", False),
    Field("last_name", "Box e, employee last name", "name", True),
    Field("suffix", "Box e, employee name suffix (Jr, III)", "name", False),
    Field("address", "Box f, employee street address", "text", True),
    Field("address_line_2", "Box f, employee second address line (apartment, suite)", "text", False),
    Field("city", "Box f, employee city", "text", True),
    Field("state", "Box f, employee state of residence (postal abbreviation)", "state", True),
    Field("zip", "Box f, employee ZIP code", "zip", True),
    Field("box1_wages", "Box 1, wages, tips, other compensation (federal taxable wages)", "money", True),
    Field("box2_fed_tax", "Box 2, federal income tax withheld", "money", True),
    Field("box3_ss_wages", "Box 3, Social Security wages", "money", True),
    Field("box4_ss_tax", "Box 4, Social Security tax withheld", "money", True),
    Field("box5_medicare_wages", "Box 5, Medicare wages and tips", "money", True),
    Field("box6_medicare_tax", "Box 6, Medicare tax withheld", "money", True),
    Field("box7_ss_tips", "Box 7, Social Security tips", "money", False),
    Field("box8_allocated_tips", "Box 8, allocated tips", "money", False),
    Field("box10_dependent_care", "Box 10, dependent care benefits", "money", False),
    Field("box11_nonqualified", "Box 11, nonqualified plans", "money", False),
    Field("statutory_employee", "Box 13, statutory employee checkbox", "flag", False),
    Field("retirement_plan", "Box 13, retirement plan checkbox", "flag", False),
    Field("third_party_sick_pay", "Box 13, third-party sick pay checkbox", "flag", False),
    Field("box14b_tipped_occupation", "Box 14b, Treasury tipped occupation code (2026 form and later)", "id", False),
    Field("box15_state", "Box 15, state of the state wages (postal abbreviation)", "state", True),
    Field("box15_state_id", "Box 15, employer's state ID number", "id", True),
    Field("box16_state_wages", "Box 16, state wages", "money", True),
    Field("box17_state_tax", "Box 17, state income tax withheld", "money", True),
)

BY_KEY = {f.key: f for f in FIELDS}
REQUIRED = [f.key for f in FIELDS if f.required]
OPTIONAL = [f.key for f in FIELDS if not f.required]


def catalog() -> list[dict]:
    """The field list as it appears in the prompt."""
    return [{"field": f.key, "description": f.description, "required": f.required} for f in FIELDS]
