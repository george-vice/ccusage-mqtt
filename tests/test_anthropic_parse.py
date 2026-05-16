from datetime import datetime, timezone

from ccusage_mqtt.anthropic_client import RateLimitSnapshot, parse_ratelimit_headers


def test_parses_full_headers():
    # Anthropic-issued ratelimit headers — names per Anthropic API docs.
    # Utilization is a 0..1 float string; reset is an RFC3339 timestamp.
    headers = {
        "anthropic-ratelimit-unified-5h-utilization": "0.42",
        "anthropic-ratelimit-unified-5h-reset": "2026-05-16T15:00:00Z",
        "anthropic-ratelimit-unified-5h-status": "allowed",
        "anthropic-ratelimit-unified-7d-utilization": "0.18",
        "anthropic-ratelimit-unified-7d-reset": "2026-05-23T10:00:00Z",
        "anthropic-ratelimit-unified-7d-status": "allowed",
    }
    snap = parse_ratelimit_headers(headers, now=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc))
    assert isinstance(snap, RateLimitSnapshot)
    assert snap.session_pct == 42.0
    assert snap.session_reset_minutes == 60
    assert snap.session_status == "allowed"
    assert snap.weekly_pct == 18.0
    assert snap.weekly_reset_minutes == 7 * 24 * 60 - 4 * 60  # 7d minus 4h elapsed today
    assert snap.weekly_status == "allowed"


def test_clamps_negative_reset_to_zero():
    """Reset already passed (clock skew or just-rolled-over)."""
    headers = {
        "anthropic-ratelimit-unified-5h-utilization": "0.0",
        "anthropic-ratelimit-unified-5h-reset": "2026-05-16T13:00:00Z",
        "anthropic-ratelimit-unified-5h-status": "allowed",
        "anthropic-ratelimit-unified-7d-utilization": "0.0",
        "anthropic-ratelimit-unified-7d-reset": "2026-05-16T13:00:00Z",
        "anthropic-ratelimit-unified-7d-status": "allowed",
    }
    snap = parse_ratelimit_headers(headers, now=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc))
    assert snap.session_reset_minutes == 0
    assert snap.weekly_reset_minutes == 0


def test_missing_headers_default_to_unknown():
    snap = parse_ratelimit_headers({}, now=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc))
    assert snap.session_pct is None
    assert snap.session_reset_minutes is None
    assert snap.session_status == "unknown"
    assert snap.weekly_pct is None
    assert snap.weekly_status == "unknown"


def test_header_lookup_is_case_insensitive():
    # `requests` returns CaseInsensitiveDict — we should match what callers
    # see, but explicitly verify our parser works with any case.
    headers = {
        "Anthropic-RateLimit-Unified-5h-Utilization": "0.5",
        "anthropic-ratelimit-unified-5h-reset": "2026-05-16T15:00:00Z",
        "ANTHROPIC-RATELIMIT-UNIFIED-5H-STATUS": "limited",
    }
    snap = parse_ratelimit_headers(headers, now=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc))
    assert snap.session_pct == 50.0
    assert snap.session_status == "limited"
