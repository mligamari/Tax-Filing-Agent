"""Keep full SSNs out of anything printed: console output, reports and error messages."""
from __future__ import annotations

import re

_SSN = re.compile(r"(?<!\d)(\d{3})-?(\d{2})-?(\d{4})(?!\d)")


def mask_ssn(ssn: str) -> str:
    digits = re.sub(r"\D", "", ssn or "")
    if len(digits) != 9:
        return "***-**-****"
    return f"***-**-{digits[-4:]}"


def mask_text(text: str) -> str:
    """Mask anything in free text that looks like an SSN."""
    return _SSN.sub(lambda m: f"***-**-{m.group(3)}", text)
