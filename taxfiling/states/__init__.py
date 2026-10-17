"""Per-state filing profiles: which records a state wants and its own rules, as JSON."""
from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = Path(__file__).parent


def available_states() -> list[str]:
    return sorted(p.stem.upper() for p in STATE_DIR.glob("*.json"))


def load_state(code: str) -> dict:
    path = STATE_DIR / f"{code.lower()}.json"
    if not path.exists():
        raise ValueError(f"No profile for state '{code}'. Available: {', '.join(available_states())}")
    return json.loads(path.read_text())


def report_profile(code: str, report: str = "w2") -> dict:
    state = load_state(code)
    try:
        profile = state["reports"][report]
    except KeyError:
        raise ValueError(f"{state['name']} has no '{report}' report profile yet") from None
    return {**profile, "state": state["state"], "state_name": state["name"], "fips": state["fips"]}
