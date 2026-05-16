from datetime import datetime, timezone

from ccusage_mqtt.anthropic_client import RateLimitSnapshot
from ccusage_mqtt.ccusage import BlockSnapshot
from ccusage_mqtt.state import State


def test_state_starts_with_all_unknown():
    s = State()
    assert s.session_pct is None
    assert s.weekly_pct is None
    assert s.tokens_used is None
    assert s.spend_so_far_usd is None
    assert s.burn_rate_pct_per_min is None
    assert s.mood == "idle"  # default while warming up
    assert s.session_status == "unknown"
    assert s.weekly_status == "unknown"


def test_apply_rate_limits_writes_six_fields():
    s = State()
    snap = RateLimitSnapshot(
        session_pct=42.0, session_reset_minutes=120, session_status="allowed",
        weekly_pct=18.0, weekly_reset_minutes=2000, weekly_status="allowed",
    )
    s.apply_rate_limits(snap)
    assert s.session_pct == 42.0
    assert s.session_reset_minutes == 120
    assert s.session_status == "allowed"
    assert s.weekly_pct == 18.0
    assert s.weekly_reset_minutes == 2000
    assert s.weekly_status == "allowed"


def test_apply_block_writes_token_and_cost_fields():
    s = State()
    block = BlockSnapshot(
        tokens_used=12345,
        spend_so_far_usd=0.987,
        block_started_at=datetime(2026, 5, 16, 15, 0, 0, tzinfo=timezone.utc),
        block_ends_at=datetime(2026, 5, 16, 20, 0, 0, tzinfo=timezone.utc),
        block_elapsed_minutes=90.0,
    )
    s.apply_block(block)
    assert s.tokens_used == 12345
    assert s.spend_so_far_usd == 0.987
    assert s.block_elapsed_minutes == 90.0


def test_status_unknown_after_repeated_header_failure():
    s = State()
    s.apply_rate_limits(RateLimitSnapshot(
        session_pct=10.0, session_reset_minutes=10, session_status="allowed",
        weekly_pct=5.0, weekly_reset_minutes=10, weekly_status="allowed",
    ))
    s.mark_headers_stale()
    assert s.session_status == "unknown"
    assert s.weekly_status == "unknown"
    # The pct fields are NOT cleared — retained values stand.
    assert s.session_pct == 10.0
