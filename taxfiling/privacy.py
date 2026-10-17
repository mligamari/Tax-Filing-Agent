"""Keep full SSNs out of anything printed: console output, reports and error messages."""
from __future__ import annotations

import re


def mask_ssn(ssn: str) -> str:
    digits = re.sub(r"\D", "", ssn or "")
    if len(digits) != 9:
        return "***-**-****"
    return f"***-**-{digits[-4:]}"
