from collections import deque
from typing import Deque, Literal


class RingBuffer:
    def __init__(self, capacity: int) -> None:
        self._buf: Deque[tuple[float, float]] = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._buf)

    def add(self, ts_sec: float, session_pct: float) -> None:
        self._buf.append((ts_sec, session_pct))

    def clear(self) -> None:
        self._buf.clear()

    def oldest(self) -> tuple[float, float]:
        return self._buf[0]

    def latest(self) -> tuple[float, float]:
        return self._buf[-1]

    def timespan_sec(self) -> float:
        if len(self._buf) < 2:
            return 0.0
        return self._buf[-1][0] - self._buf[0][0]


def compute_rate(rb: RingBuffer, *, min_window_sec: float) -> float | None:
    """%/min over the buffer, or None if not enough data.

    Ports usage_rate.cpp:57-72 — same guards, same formula.
    """
    if len(rb) < 2:
        return None
    dt_sec = rb.timespan_sec()
    if dt_sec < min_window_sec:
        return None
    dp = rb.latest()[1] - rb.oldest()[1]
    if dp < 0.0:
        dp = 0.0
    return dp * 60.0 / dt_sec


def detect_reset(rb: RingBuffer, *, new_pct: float) -> bool:
    """True when a new sample's pct dropped ≥5 vs the latest in the buffer.

    Ports usage_rate.cpp:44-49 — Anthropic's 5h window rolled over.
    """
    if len(rb) == 0:
        return False
    return new_pct + 5.0 < rb.latest()[1]


Mood = Literal["idle", "normal", "active", "heavy"]


def classify_mood(
    rate_pct_per_min: float | None,
    *,
    idle_below: float,
    normal_below: float,
    active_below: float,
) -> Mood:
    """Map %/min to a mood bucket. Ports usage_rate.cpp:69-72 thresholds.

    Note the asymmetry: rates exactly equal to a threshold fall into the
    *upper* bucket. The firmware uses `< threshold` checks; this matches that.
    """
    if rate_pct_per_min is None:
        return "idle"
    if rate_pct_per_min < idle_below:
        return "idle"
    if rate_pct_per_min < normal_below:
        return "normal"
    if rate_pct_per_min < active_below:
        return "active"
    return "heavy"
