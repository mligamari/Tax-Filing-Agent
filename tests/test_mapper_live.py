"""Optional: ask the real Claude API to map the messy workbook. Skipped unless asked for.

    TAXFILING_LIVE_CLAUDE=1 ANTHROPIC_API_KEY=... python -m pytest tests/test_mapper_live.py -s

It sends only what `taxfiling map --dry-run` prints (headers and value shapes), after the
same two checks as any other run, and writes the audit log to the test's temp folder.
"""
import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TAXFILING_LIVE_CLAUDE") != "1",
                                reason="set TAXFILING_LIVE_CLAUDE=1 to call the real Claude API")


def test_claude_maps_the_messy_workbook(tmp_path):
    pytest.importorskip("anthropic")
    from examples.va_2025_messy.make_messy_workbook import EXPECTED
    from taxfiling.mapper import AuditLog, check_proposal, format_review, prepare
    from taxfiling.mapper.providers import ClaudeProvider

    from .conftest import MESSY_WORKBOOK
    prepared = prepare(MESSY_WORKBOOK)
    log = tmp_path / "audit.jsonl"
    answer = ClaudeProvider(prepared.deny, AuditLog(log)).propose(prepared.payload, prepared.profile)
    mapping, review = check_proposal(answer, prepared.profile)
    print(format_review(review, "claude"))
    print(f"audit log: {log}")
    assert mapping is not None and review.complete
    wrong = {k: (v, mapping["columns"].get(k)) for k, v in EXPECTED.items() if mapping["columns"].get(k) != v}
    assert not wrong, json.dumps(wrong, indent=1)
    assert len(mapping["box12"]) == 4
