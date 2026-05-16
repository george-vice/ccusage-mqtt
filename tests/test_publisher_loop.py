import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ccusage_mqtt.anthropic_client import RateLimitSnapshot, AnthropicProbeError
from ccusage_mqtt.ccusage import BlockSnapshot, CcusageError
from ccusage_mqtt.publisher import PublisherLoop, LoopConfig


def make_loop(*, header_poll, ccusage_poll, header_sec=60, ccusage_sec=30):
    mqtt = MagicMock()
    return PublisherLoop(
        cfg=LoopConfig(
            base_topic="ct",
            header_poll_sec=header_sec,
            ccusage_poll_sec=ccusage_sec,
            burn_rate_window_sec=240,
            idle_below=0.10,
            normal_below=0.20,
            active_below=0.33,
        ),
        mqtt=mqtt,
        poll_headers=header_poll,
        poll_ccusage=ccusage_poll,
    ), mqtt


def test_tick_polls_only_when_due():
    header = MagicMock(return_value=RateLimitSnapshot(
        session_pct=30.0, session_reset_minutes=60, session_status="allowed",
        weekly_pct=10.0, weekly_reset_minutes=2000, weekly_status="allowed",
    ))
    ccu = MagicMock(return_value=BlockSnapshot(
        tokens_used=100, spend_so_far_usd=0.01,
        block_started_at=datetime(2026, 5, 16, 15, 0, 0, tzinfo=timezone.utc),
        block_ends_at=datetime(2026, 5, 16, 20, 0, 0, tzinfo=timezone.utc),
        block_elapsed_minutes=60.0,
    ))
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)

    # First tick — both pollers fire
    loop.tick(now_monotonic=0.0)
    assert header.call_count == 1
    assert ccu.call_count == 1

    # 15s later — neither is due
    loop.tick(now_monotonic=15.0)
    assert header.call_count == 1
    assert ccu.call_count == 1

    # 30s — ccusage due, headers not
    loop.tick(now_monotonic=30.0)
    assert header.call_count == 1
    assert ccu.call_count == 2

    # 60s — both due
    loop.tick(now_monotonic=60.0)
    assert header.call_count == 2
    assert ccu.call_count == 3


def test_tick_publishes_state_after_pollers():
    header = MagicMock(return_value=RateLimitSnapshot(
        session_pct=30.0, session_reset_minutes=60, session_status="allowed",
        weekly_pct=10.0, weekly_reset_minutes=2000, weekly_status="allowed",
    ))
    ccu = MagicMock(return_value=None)  # no active block
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)

    loop.tick(now_monotonic=0.0)
    mqtt.publish_state.assert_called_once()
    args = mqtt.publish_state.call_args
    payloads = args.kwargs["payloads"]
    # Every payload now carries "account" (defaults to "default" when unset);
    # check only the "value" half so this test stays focused on loop behavior.
    assert payloads["session_pct"]["value"] == 30.0
    assert payloads["session_status"]["value"] == "allowed"
    assert payloads["mood"]["value"] == "idle"  # warm-up — only 1 sample
    assert payloads["burn_rate_pct_per_min"]["value"] is None


def test_burn_rate_warms_up_after_window():
    header_responses = iter([
        RateLimitSnapshot(session_pct=p, session_reset_minutes=120, session_status="allowed",
                          weekly_pct=10.0, weekly_reset_minutes=2000, weekly_status="allowed")
        for p in [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    ])
    header = MagicMock(side_effect=lambda: next(header_responses))
    ccu = MagicMock(return_value=None)
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)

    # 5 samples at 60s intervals → spans 240s exactly → rate = (14-10)/4 = 1.0 %/min
    for i in range(5):
        loop.tick(now_monotonic=i * 60.0)

    final = mqtt.publish_state.call_args.kwargs["payloads"]
    assert final["burn_rate_pct_per_min"]["value"] == pytest.approx(1.0)
    assert final["mood"]["value"] == "heavy"


def test_session_reset_flushes_ring():
    # First sample 80%, second 78% (no reset), third 70% (≥5 drop → reset)
    samples = [80.0, 78.0, 70.0]
    snaps = iter([
        RateLimitSnapshot(session_pct=p, session_reset_minutes=120, session_status="allowed",
                          weekly_pct=10.0, weekly_reset_minutes=2000, weekly_status="allowed")
        for p in samples
    ])
    header = MagicMock(side_effect=lambda: next(snaps))
    ccu = MagicMock(return_value=None)
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)
    for i, _ in enumerate(samples):
        loop.tick(now_monotonic=i * 60.0)
    # After reset detection, ring should have only the latest sample → mood=idle
    assert loop._state.mood == "idle"
    assert len(loop._ring) == 1


def test_header_failure_keeps_loop_running():
    header = MagicMock(side_effect=AnthropicProbeError("network"))
    ccu = MagicMock(return_value=None)
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)
    loop.tick(now_monotonic=0.0)
    # 3 consecutive failures → status goes "unknown"
    loop.tick(now_monotonic=60.0)
    loop.tick(now_monotonic=120.0)
    final = mqtt.publish_state.call_args.kwargs["payloads"]
    assert final["session_status"]["value"] == "unknown"


def test_ccusage_failure_keeps_loop_running():
    header_snap = RateLimitSnapshot(
        session_pct=30.0, session_reset_minutes=60, session_status="allowed",
        weekly_pct=10.0, weekly_reset_minutes=2000, weekly_status="allowed",
    )
    header = MagicMock(return_value=header_snap)
    ccu = MagicMock(side_effect=CcusageError("nope"))
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)
    loop.tick(now_monotonic=0.0)
    final = mqtt.publish_state.call_args.kwargs["payloads"]
    assert final["session_pct"]["value"] == 30.0  # header still flows
    assert final["tokens_used"]["value"] is None  # ccusage missing


def test_rate_limited_backs_off_header_poll():
    snap = RateLimitSnapshot(
        session_pct=99.0, session_reset_minutes=10, session_status="limited",
        weekly_pct=50.0, weekly_reset_minutes=2000, weekly_status="allowed",
    )
    from ccusage_mqtt.anthropic_client import AnthropicRateLimited
    header = MagicMock(side_effect=AnthropicRateLimited(snap))
    ccu = MagicMock(return_value=None)
    loop, mqtt = make_loop(header_poll=header, ccusage_poll=ccu)

    loop.tick(now_monotonic=0.0)
    assert header.call_count == 1
    # Normally header would re-fire at 60s; backoff pushes it to ~300s.
    loop.tick(now_monotonic=120.0)
    assert header.call_count == 1
    loop.tick(now_monotonic=305.0)
    assert header.call_count == 2

    # State should still carry the snapshot from the 429 response.
    final = mqtt.publish_state.call_args.kwargs["payloads"]
    assert final["session_status"]["value"] == "limited"
    assert final["session_pct"]["value"] == 99.0
