from datetime import datetime, timezone

from ccusage_mqtt.anthropic_client import RateLimitSnapshot
from ccusage_mqtt.ccusage import BlockSnapshot
from ccusage_mqtt.state import DerivationConfig, State


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
    # is_enterprise defaults to False on the snapshot dataclass
    assert s.is_enterprise is False


def test_apply_rate_limits_propagates_is_enterprise():
    s = State()
    s.apply_rate_limits(RateLimitSnapshot(
        session_pct=0.0, session_reset_minutes=20000, session_status="allowed",
        weekly_pct=None, weekly_reset_minutes=None, weekly_status="unknown",
        is_enterprise=True,
    ))
    assert s.is_enterprise is True


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
    # apply_block rounds spend to cents to kill ccusage's float noise.
    assert s.spend_so_far_usd == 0.99
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


def test_enterprise_mood_uses_tokens_per_hour_not_burn_rate():
    """On Enterprise, session_pct is overage-utilization (0 until you blow
    past base allocation), so burn_rate is always 0. Mood must come from
    tokens/hour instead so the user sees real activity."""
    s = State()
    s.is_enterprise = True
    s.session_pct = 0.0
    s.tokens_used = 5000
    s.block_elapsed_minutes = 60.0  # 1h elapsed → tokens_per_hour = 5000
    s.recompute_derived(
        burn_rate=0.0,
        cfg=DerivationConfig(
            tokens_idle_below=500,
            tokens_normal_below=2500,
            tokens_active_below=10000,
        ),
    )
    assert s.tokens_per_hour == 5000
    # 5000 falls in [2500, 10000) → active, even though burn_rate=0 would mean idle.
    assert s.mood == "active"


def test_enterprise_mood_is_idle_during_warmup_when_tokens_per_hour_unknown():
    s = State()
    s.is_enterprise = True
    s.session_pct = 0.0
    s.tokens_used = 100
    s.block_elapsed_minutes = 0.1  # too early — tokens_per_hour will be None
    s.recompute_derived(burn_rate=None)
    assert s.tokens_per_hour is None
    assert s.mood == "idle"


def test_pro_mood_still_uses_burn_rate_with_tokens_present():
    """Sanity-check: Pro/Max should ignore tokens_per_hour for mood."""
    s = State()
    s.is_enterprise = False
    s.session_pct = 50.0
    s.tokens_used = 50_000  # heavy by token thresholds
    s.block_elapsed_minutes = 60.0
    s.recompute_derived(burn_rate=0.05)  # idle by %/min thresholds
    assert s.tokens_per_hour == 50_000
    assert s.mood == "idle"  # honors burn_rate path, not tokens


def test_to_mqtt_payloads_returns_all_15_sensors():
    s = State()
    payloads = s.to_mqtt_payloads()
    expected_keys = {
        "session_pct", "session_reset_minutes", "session_status",
        "weekly_pct", "weekly_reset_minutes", "weekly_status",
        "burn_rate_pct_per_min", "mood",
        "time_to_limit_minutes", "block_elapsed_pct",
        "tokens_used", "tokens_per_hour",
        "spend_so_far_usd", "spend_per_hour_usd",
        "account",  # dedicated sensor surfacing the account label in the HA card
    }
    assert set(payloads.keys()) == expected_keys
    assert len(payloads) == 15


def test_to_mqtt_payloads_wraps_values_in_json_envelope_with_default_account():
    s = State()
    s.session_pct = 42.0
    payloads = s.to_mqtt_payloads()
    # account defaults to "default" so it's always visible to subscribers / HA
    assert payloads["session_pct"] == {"value": 42.0, "account": "default"}
    assert payloads["mood"] == {"value": "idle", "account": "default"}
    assert payloads["session_status"] == {"value": "unknown", "account": "default"}
    assert payloads["tokens_used"] == {"value": None, "account": "default"}
    assert payloads["account"] == {"value": "default", "account": "default"}


def test_to_mqtt_payloads_uses_provided_account():
    s = State()
    s.session_pct = 42.0
    payloads = s.to_mqtt_payloads(account="work")
    assert payloads["session_pct"] == {"value": 42.0, "account": "work"}
    assert payloads["account"] == {"value": "work", "account": "work"}
    assert all(p.get("account") == "work" for p in payloads.values())


def test_to_mqtt_payloads_falsy_account_falls_back_to_default():
    s = State()
    for account in (None, ""):
        payloads = s.to_mqtt_payloads(account=account)
        assert payloads["account"]["value"] == "default"
        assert payloads["session_pct"]["account"] == "default"
