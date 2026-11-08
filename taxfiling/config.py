"""Load the submitter / employer-contact settings that a W-2 spreadsheet does not carry."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model import Address, Contact, Employer, Submitter


def _address(d: dict) -> Address:
    zip_code = re.sub(r"\D", "", str(d.get("zip", "")))
    return Address(
        delivery=d.get("delivery_address", ""), location=d.get("location_address", ""),
        city=d.get("city", ""), state=d.get("state", "").upper(),
        zip=zip_code[:5], zip_ext=zip_code[5:9],
    )


def _contact(d: dict) -> Contact:
    return Contact(
        name=d.get("name", ""), phone=re.sub(r"\D", "", str(d.get("phone", ""))),
        phone_ext=str(d.get("phone_ext", "")), email=d.get("email", ""),
        fax=re.sub(r"\D", "", str(d.get("fax", ""))),
    )


def load_submitter(path: str | Path) -> tuple[Submitter, dict]:
    """Returns the Submitter and the raw 'employer' overrides (contact, codes)."""
    data = json.loads(Path(path).read_text())
    s = data["submitter"]
    submitter = Submitter(
        ein=re.sub(r"\D", "", str(s["ein"])),
        user_id=s.get("bso_user_id", ""),
        name=s["name"],
        address=_address(s["address"]),
        contact=_contact(s["contact"]),
        software_code=str(s.get("software_code", "98")),
        preparer_code=s.get("preparer_code", "L"),
        resub=bool(s.get("resubmission", False)),
        resub_wfid=s.get("resub_wfid", ""),
    )
    return submitter, data.get("employer", {})


def apply_employer_settings(employer: Employer, settings: dict, submitter: Submitter) -> Employer:
    """Fill RE fields the spreadsheet lacks. The employer contact defaults to the submitter contact."""
    employer.contact = _contact(settings["contact"]) if "contact" in settings else submitter.contact
    employer.kind_of_employer = settings.get("kind_of_employer", employer.kind_of_employer)
    employer.employment_code = settings.get("employment_code", employer.employment_code)
    employer.terminating_business = bool(settings.get("terminating_business", False))
    return employer
