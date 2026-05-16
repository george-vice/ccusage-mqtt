from ccusage_mqtt.usage_rate import RingBuffer, compute_rate, detect_reset


def test_ring_buffer_starts_empty():
    rb = RingBuffer(capacity=6)
    assert len(rb) == 0
    assert rb.timespan_sec() == 0.0


def test_ring_buffer_fifo_wrap():
    rb = RingBuffer(capacity=3)
    for ts, pct in [(0.0, 10.0), (1.0, 11.0), (2.0, 12.0), (3.0, 13.0)]:
        rb.add(ts, pct)
    assert len(rb) == 3
    assert rb.oldest() == (1.0, 11.0)
    assert rb.latest() == (3.0, 13.0)
    assert rb.timespan_sec() == 2.0


def test_ring_buffer_clear():
    rb = RingBuffer(capacity=6)
    rb.add(0.0, 10.0)
    rb.clear()
    assert len(rb) == 0


def test_compute_rate_returns_none_when_cold():
    rb = RingBuffer(capacity=6)
    assert compute_rate(rb, min_window_sec=240) is None

    rb.add(0.0, 10.0)
    assert compute_rate(rb, min_window_sec=240) is None


def test_compute_rate_returns_none_when_window_too_short():
    rb = RingBuffer(capacity=6)
    rb.add(0.0, 10.0)
    rb.add(60.0, 11.0)
    assert compute_rate(rb, min_window_sec=240) is None


def test_compute_rate_linear_pct_per_min():
    rb = RingBuffer(capacity=6)
    # 4-minute window, 1 pct rise per minute
    for i in range(5):
        rb.add(i * 60.0, 10.0 + i * 1.0)
    rate = compute_rate(rb, min_window_sec=240)
    assert rate is not None
    assert abs(rate - 1.0) < 1e-6


def test_compute_rate_clamps_negative_to_zero():
    rb = RingBuffer(capacity=6)
    # Session decreases (which we don't expect, but be defensive)
    for i in range(5):
        rb.add(i * 60.0, 10.0 - i * 1.0)
    rate = compute_rate(rb, min_window_sec=240)
    assert rate == 0.0


def test_detect_reset_triggers_on_5pct_drop():
    rb = RingBuffer(capacity=6)
    rb.add(0.0, 50.0)
    rb.add(60.0, 51.0)
    assert detect_reset(rb, new_pct=45.9) is True  # 51 - 45.9 = 5.1 ≥ 5


def test_detect_reset_ignores_small_drop():
    rb = RingBuffer(capacity=6)
    rb.add(0.0, 50.0)
    rb.add(60.0, 51.0)
    assert detect_reset(rb, new_pct=47.0) is False  # 51 - 47 = 4 < 5


def test_detect_reset_false_when_empty():
    rb = RingBuffer(capacity=6)
    assert detect_reset(rb, new_pct=10.0) is False


import pytest
from ccusage_mqtt.usage_rate import classify_mood, Mood


@pytest.mark.parametrize("rate,expected", [
    (None,  "idle"),     # warm-up
    (0.0,   "idle"),
    (0.05,  "idle"),
    (0.09,  "idle"),
    (0.10,  "normal"),   # at threshold → next bucket (firmware uses < 0.10 → idle)
    (0.15,  "normal"),
    (0.20,  "active"),   # at threshold
    (0.30,  "active"),
    (0.33,  "heavy"),    # at threshold
    (0.50,  "heavy"),
    (1.0,   "heavy"),
])
def test_classify_mood(rate: float | None, expected: str):
    mood = classify_mood(
        rate,
        idle_below=0.10,
        normal_below=0.20,
        active_below=0.33,
    )
    assert mood == expected


def test_mood_literal_values():
    # Enum values must match HA discovery options exactly
    assert set(Mood.__args__) == {"idle", "normal", "active", "heavy"}


def test_golden_sequence_matches_firmware():
    """Golden test: a recorded sequence of (ts_sec, session_pct) samples
    must produce the same mood-per-poll as Clawdmeter's usage_rate.cpp.

    Sequence: 0 → 100% over 5 hours = 100/300 = 0.333 %/min → heavy after warm-up.
    """
    rb = RingBuffer(capacity=6)
    moods: list[str] = []
    # Add 10 samples at 60s intervals, simulating 0.333 %/min growth
    for i in range(10):
        ts = i * 60.0
        pct = i * (100.0 / 300.0)  # 0.333 %/min
        if detect_reset(rb, new_pct=pct):
            rb.clear()
        rb.add(ts, pct)
        rate = compute_rate(rb, min_window_sec=240)
        moods.append(classify_mood(rate, idle_below=0.10, normal_below=0.20, active_below=0.33))

    # Warm-up: first 4 samples (0,60,120,180s) span < 240s → idle
    assert moods[:4] == ["idle", "idle", "idle", "idle"]
    # By sample 5 (i=4, ts=240s) timespan = 240s ≥ 240 → rate ≈ 0.333 → heavy
    assert moods[4:] == ["heavy"] * 6
