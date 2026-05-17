# tests/test_ccusage_parse.py
import json
from datetime import datetime, timezone
from pathlib import Path

from ccusage_mqtt.ccusage import BlockSnapshot, parse_blocks_json


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_returns_active_block():
    raw = (FIXTURES / "ccusage_active.json").read_text()
    snap = parse_blocks_json(raw, now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))
    assert isinstance(snap, BlockSnapshot)
    assert snap.tokens_used == 20000
    assert abs(snap.spend_so_far_usd - 0.42) < 1e-9
    # Block started at 15:00, "now" is 16:00 → 1h elapsed = 60 min
    assert snap.block_elapsed_minutes == 60


def test_parse_returns_none_when_no_active():
    raw = (FIXTURES / "ccusage_no_active.json").read_text()
    snap = parse_blocks_json(raw, now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))
    assert snap is None


def test_parse_returns_none_on_empty_blocks():
    snap = parse_blocks_json('{"blocks": []}', now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))
    assert snap is None


def test_parse_raises_on_malformed_json():
    import pytest
    with pytest.raises(ValueError):
        parse_blocks_json("not json", now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))


def test_parse_handles_missing_token_subfields():
    """Defensive: if a subfield is absent, treat it as 0."""
    raw = json.dumps({
        "blocks": [{
            "id": "b", "startTime": "2026-05-16T15:00:00Z", "endTime": "2026-05-16T20:00:00Z",
            "isActive": True,
            "tokenCounts": {"inputTokens": 100},  # only one field present
            "costUSD": 0.0, "models": []
        }]
    })
    snap = parse_blocks_json(raw, now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))
    assert snap is not None
    assert snap.tokens_used == 100
