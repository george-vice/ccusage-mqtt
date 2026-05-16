from collections import deque
from typing import Deque


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
