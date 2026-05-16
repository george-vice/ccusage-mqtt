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


def test_recompute_derived_with_full_data():
    s = State()
    s.session_pct = 50.0
    s.session_reset_minutes = 120  # 2h left of 5h block → 3h elapsed = 60% elapsed
    s.tokens_used = 18000
    s.spend_so_far_usd = 0.30
    s.block_elapsed_minutes = 180.0  # 3h elapsed → 3.0 hours
    s.recompute_derived(burn_rate=0.25)

    assert s.burn_rate_pct_per_min == 0.25
    assert s.mood == "active"  # 0.25 falls in [0.20, 0.33)
    assert abs(s.time_to_limit_minutes - 200.0) < 1e-6  # (100 - 50) / 0.25
    assert s.block_elapsed_pct == 60.0
    assert abs(s.tokens_per_hour - 6000.0) < 1e-6
    assert abs(s.spend_per_hour_usd - 0.10) < 1e-6


def test_recompute_derived_during_warmup():
    s = State()
    s.session_pct = 10.0
    s.session_reset_minutes = 300  # full window left
    s.tokens_used = 100
    s.spend_so_far_usd = 0.01
    s.block_elapsed_minutes = 0.5  # < 1 min — too early for rates
    s.recompute_derived(burn_rate=None)

    assert s.burn_rate_pct_per_min is None
    assert s.mood == "idle"
    assert s.time_to_limit_minutes is None
    assert s.block_elapsed_pct == 0.0
    assert s.tokens_per_hour is None
    assert s.spend_per_hour_usd is None


def test_recompute_derived_zero_rate_means_no_eta():
    s = State()
    s.session_pct = 30.0
    s.session_reset_minutes = 120
    s.block_elapsed_minutes = 180.0
    s.recompute_derived(burn_rate=0.0)
    assert s.mood == "idle"
    assert s.time_to_limit_minutes is None


def test_recompute_derived_handles_missing_session_pct():
    s = State()
    s.recompute_derived(burn_rate=0.25)
    assert s.time_to_limit_minutes is None
    assert s.block_elapsed_pct is None


def test_to_mqtt_payloads_returns_all_14_sensors():
    s = State()
    payloads = s.to_mqtt_payloads()
    expected_keys = {
        "session_pct", "session_reset_minutes", "session_status",
        "weekly_pct", "weekly_reset_minutes", "weekly_status",
        "burn_rate_pct_per_min", "mood",
        "time_to_limit_minutes", "block_elapsed_pct",
        "tokens_used", "tokens_per_hour",
        "spend_so_far_usd", "spend_per_hour_usd",
    }
    assert set(payloads.keys()) == expected_keys
    assert len(payloads) == 14


def test_to_mqtt_payloads_wraps_values_in_json_envelope():
    s = State()
    s.session_pct = 42.0
    payloads = s.to_mqtt_payloads()
    assert payloads["session_pct"] == {"value": 42.0}
    assert payloads["mood"] == {"value": "idle"}
    assert payloads["session_status"] == {"value": "unknown"}
    assert payloads["tokens_used"] == {"value": None}
