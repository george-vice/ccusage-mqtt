from datetime import datetime, timezone

from ccusage_mqtt.anthropic_client import RateLimitSnapshot, parse_ratelimit_headers


# Anthropic returns reset timestamps as Unix epoch *seconds* in a string
# (verified against Clawdmeter daemon, not ISO 8601).
_NOW = datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc)
_NOW_TS = _NOW.timestamp()


def test_parses_full_headers():
    headers = {
        "anthropic-ratelimit-unified-5h-utilization": "0.42",
        "anthropic-ratelimit-unified-5h-reset": str(_NOW_TS + 3600),  # 60 min from now
        "anthropic-ratelimit-unified-5h-status": "allowed",
        "anthropic-ratelimit-unified-7d-utilization": "0.18",
        "anthropic-ratelimit-unified-7d-reset": str(_NOW_TS + 7 * 24 * 3600 - 4 * 3600),
        "anthropic-ratelimit-unified-7d-status": "allowed",
    }
    snap = parse_ratelimit_headers(headers, now=_NOW)
    assert isinstance(snap, RateLimitSnapshot)
    assert snap.session_pct == 42.0
    assert snap.session_reset_minutes == 60
    assert snap.session_status == "allowed"
    assert snap.weekly_pct == 18.0
    assert snap.weekly_reset_minutes == 7 * 24 * 60 - 4 * 60
    assert snap.weekly_status == "allowed"


def test_clamps_negative_reset_to_zero():
    """Reset already passed (clock skew or just-rolled-over)."""
    headers = {
        "anthropic-ratelimit-unified-5h-utilization": "0.0",
        "anthropic-ratelimit-unified-5h-reset": str(_NOW_TS - 3600),  # 1h ago
        "anthropic-ratelimit-unified-5h-status": "allowed",
        "anthropic-ratelimit-unified-7d-utilization": "0.0",
        "anthropic-ratelimit-unified-7d-reset": str(_NOW_TS - 3600),
        "anthropic-ratelimit-unified-7d-status": "allowed",
    }
    snap = parse_ratelimit_headers(headers, now=_NOW)
    assert snap.session_reset_minutes == 0
    assert snap.weekly_reset_minutes == 0


def test_missing_headers_default_to_unknown():
    snap = parse_ratelimit_headers({}, now=_NOW)
    assert snap.session_pct is None
    assert snap.session_reset_minutes is None
    assert snap.session_status == "unknown"
    assert snap.weekly_pct is None
    assert snap.weekly_status == "unknown"


def test_header_lookup_is_case_insensitive():
    headers = {
        "Anthropic-RateLimit-Unified-5h-Utilization": "0.5",
        "anthropic-ratelimit-unified-5h-reset": str(_NOW_TS + 3600),
        "ANTHROPIC-RATELIMIT-UNIFIED-5H-STATUS": "limited",
    }
    snap = parse_ratelimit_headers(headers, now=_NOW)
    assert snap.session_pct == 50.0
    assert snap.session_status == "limited"


def test_parse_pct_no_float_artifacts():
    """`0.07 * 100.0` in IEEE-754 is 7.000000000000001 — parser must round it out."""
    headers = {"anthropic-ratelimit-unified-5h-utilization": "0.07"}
    snap = parse_ratelimit_headers(headers, now=_NOW)
    assert snap.session_pct == 7.0


def test_malformed_reset_returns_none():
    """If the header isn't a numeric string, we get None, not a crash."""
    headers = {
        "anthropic-ratelimit-unified-5h-utilization": "0.30",
        "anthropic-ratelimit-unified-5h-reset": "not-a-number",
        "anthropic-ratelimit-unified-5h-status": "allowed",
    }
    snap = parse_ratelimit_headers(headers, now=_NOW)
    assert snap.session_pct == 30.0
    assert snap.session_reset_minutes is None
