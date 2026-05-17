# ccusage-mqtt Implementation Plan

> **HISTORICAL — DO NOT FOLLOW VERBATIM.** This is the *original* step-by-step
> implementation plan, written before the project shipped. It calls for an
> `ANTHROPIC_API_KEY` env var and `x-api-key` header auth; **both were removed
> in the OAuth refactor (commit `5973a1f`)**. The shipped code uses the
> Claude Code OAuth token from `~/.claude/.credentials.json` instead — no
> Anthropic API key is required, ever.
>
> For the authoritative current design see `docs/superpowers/specs/2026-05-16-ccusage-mqtt-design.md`
> and `CLAUDE.md`. This file is kept for process / archaeology only.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Docker container that publishes Claude Code usage telemetry to MQTT with Home Assistant auto-discovery — 14 sensors under a `Claude Code Usage` device, including a `mood` sensor that mirrors the Clawdmeter firmware's classification.

**Architecture:** Single-threaded Python event loop with two pollers (Anthropic ratelimit headers every 60s, `ccusage` CLI every 30s) feeding a shared `State`. State is serialized to MQTT JSON payloads and published with retain. Mood/burn-rate are derived from a ring buffer that ports `Clawdmeter/firmware/src/usage_rate.cpp` line-for-line so calibration matches.

**Tech Stack:** Python 3.12, `paho-mqtt` 2.x, `requests` 2.31+, pytest + `responses` (HTTP mocking) + `pytest-mock`. Container base `node:22-alpine` + Python 3 + `ccusage` (pinned npm version). MQTT broker: any reachable MQTT broker (the Mosquitto add-on on Home Assistant is the typical choice).

**Repo:** `~/code/ccusage-mqtt/` (already exists with spec + .gitignore committed at `5b88851`)

**Spec reference:** `docs/superpowers/specs/2026-05-16-ccusage-mqtt-design.md`

---

## Conventions used across all tasks

- All file paths are relative to `~/code/ccusage-mqtt/`.
- All `python` invocations assume an activated venv created with `python3 -m venv .venv` (Task 1 sets this up). If `python3 -m venv` is unavailable on the build host, fall back to `python3 -m pip install --user --break-system-packages` for dev deps; never `--break-system-packages` without `--user` outside the container.
- All commits are made on the default branch `main`. (Single-developer personal repo per spec §1.)
- Test layout: `tests/test_<module>.py` for each module.
- Tests run with `python -m pytest tests/ -v`.
- No docstrings on private helpers. Public dataclasses get one line.
- Type hints required on every function signature.

---

## Task 1: Repo scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `ccusage_mqtt/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `.env.example`
- Create: `README.md`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "ccusage-mqtt"
version = "0.1.0"
description = "Publish Claude Code usage telemetry to MQTT for Home Assistant"
requires-python = ">=3.12"
dependencies = [
    "paho-mqtt>=2.1,<3",
    "requests>=2.31,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
    "responses>=0.25",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --tb=short"

[tool.setuptools.packages.find]
include = ["ccusage_mqtt*"]
```

- [ ] **Step 2: Write `.env.example`**

```bash
# MQTT broker — typically your Home Assistant host running the Mosquitto add-on
MQTT_HOST=homeassistant.local
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_CLIENT_ID=ccusage-mqtt
MQTT_DISCOVERY_PREFIX=homeassistant
MQTT_BASE_TOPIC=claude_code_usage

# Anthropic API (probe request for ratelimit headers)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_API_BASE=https://api.anthropic.com
PROBE_MODEL=claude-haiku-4-5-20251001

# ccusage subprocess
CCUSAGE_PROJECTS_DIR=/data/claude-projects

# Polling cadences (seconds)
HEADER_POLL_SEC=60
CCUSAGE_POLL_SEC=30

# Burn-rate ring buffer
BURN_RATE_WINDOW_SEC=240
MOOD_IDLE_BELOW=0.10
MOOD_NORMAL_BELOW=0.20
MOOD_ACTIVE_BELOW=0.33

LOG_LEVEL=INFO
```

- [ ] **Step 3: Write `README.md`**

```markdown
# ccusage-mqtt

Publishes Claude Code usage telemetry to MQTT with Home Assistant auto-discovery.
Mirrors the [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) firmware's
telemetry surface so the same `mood` thresholds apply.

See `docs/superpowers/specs/2026-05-16-ccusage-mqtt-design.md` for the full design.

## Quick start

    cp .env.example .env && $EDITOR .env
    docker compose up -d --build
    docker compose logs -f

Home Assistant discovers the `Claude Code Usage` device with 14 sensors within ~60s.
```

- [ ] **Step 4: Create empty package files**

```bash
touch ccusage_mqtt/__init__.py tests/__init__.py
```

- [ ] **Step 5: Create venv + install dev deps**

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import paho.mqtt.client; import responses; import pytest; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml ccusage_mqtt/ tests/ .env.example README.md
git commit -m "feat: scaffold ccusage-mqtt package + dev deps"
```

---

## Task 2: usage_rate — RingBuffer

**Files:**
- Create: `ccusage_mqtt/usage_rate.py`
- Test: `tests/test_usage_rate.py`

The buffer holds (monotonic_time_seconds, session_pct) samples, fixed capacity 6 (matches firmware `RING_SIZE`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_usage_rate.py
from ccusage_mqtt.usage_rate import RingBuffer


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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_usage_rate.py -v
```

Expected: `ModuleNotFoundError: No module named 'ccusage_mqtt.usage_rate'`

- [ ] **Step 3: Implement `RingBuffer`**

```python
# ccusage_mqtt/usage_rate.py
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_usage_rate.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/usage_rate.py tests/test_usage_rate.py
git commit -m "feat(usage_rate): RingBuffer with FIFO wrap + timespan"
```

---

## Task 3: usage_rate — compute_rate + session-reset detection

**Files:**
- Modify: `ccusage_mqtt/usage_rate.py`
- Modify: `tests/test_usage_rate.py`

Ports `Clawdmeter/firmware/src/usage_rate.cpp:36-72`. `compute_rate()` returns `None` if not warmed up. `detect_reset()` flushes when new sample drops ≥5%.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_usage_rate.py`:

```python
from ccusage_mqtt.usage_rate import compute_rate, detect_reset


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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_usage_rate.py -v
```

Expected: 6 new tests fail with `ImportError: cannot import name 'compute_rate' from 'ccusage_mqtt.usage_rate'` (or similar).

- [ ] **Step 3: Implement `compute_rate` and `detect_reset`**

Append to `ccusage_mqtt/usage_rate.py`:

```python
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
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_usage_rate.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/usage_rate.py tests/test_usage_rate.py
git commit -m "feat(usage_rate): compute_rate (warm-up + linear %/min) + reset detection"
```

---

## Task 4: usage_rate — classify_mood + golden test against firmware

**Files:**
- Modify: `ccusage_mqtt/usage_rate.py`
- Modify: `tests/test_usage_rate.py`

Mood is an enum-like string. `None` rate → `"idle"` (matches firmware fallback when not warmed up).

- [ ] **Step 1: Add failing tests**

Append to `tests/test_usage_rate.py`:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_usage_rate.py -v
```

Expected: 13 new tests fail with import errors.

- [ ] **Step 3: Implement `classify_mood` + `Mood` type**

Append to `ccusage_mqtt/usage_rate.py`:

```python
from typing import Literal

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
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_usage_rate.py -v
```

Expected: 23 passed total.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/usage_rate.py tests/test_usage_rate.py
git commit -m "feat(usage_rate): classify_mood + golden test vs firmware thresholds"
```

---

## Task 5: anthropic_client — parse_ratelimit_headers

**Files:**
- Create: `ccusage_mqtt/anthropic_client.py`
- Test: `tests/test_anthropic_parse.py`

Parses the `anthropic-ratelimit-unified-*` HTTP response headers into a `RateLimitSnapshot` dataclass. Pure function — no I/O.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_anthropic_parse.py
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_anthropic_parse.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement parser**

```python
# ccusage_mqtt/anthropic_client.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping

Status = Literal["allowed", "limited", "unknown"]


@dataclass(frozen=True)
class RateLimitSnapshot:
    session_pct: float | None
    session_reset_minutes: int | None
    session_status: Status
    weekly_pct: float | None
    weekly_reset_minutes: int | None
    weekly_status: Status


def _get_ci(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return None


def _parse_pct(headers: Mapping[str, str], name: str) -> float | None:
    raw = _get_ci(headers, name)
    if raw is None:
        return None
    try:
        return float(raw) * 100.0
    except ValueError:
        return None


def _parse_reset_minutes(headers: Mapping[str, str], name: str, *, now: datetime) -> int | None:
    raw = _get_ci(headers, name)
    if raw is None:
        return None
    try:
        # Anthropic uses RFC3339; "Z" is +00:00
        reset_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta_sec = (reset_at - now).total_seconds()
    return max(0, round(delta_sec / 60))


def _parse_status(headers: Mapping[str, str], name: str) -> Status:
    raw = _get_ci(headers, name)
    if raw in ("allowed", "limited"):
        return raw
    return "unknown"


def parse_ratelimit_headers(
    headers: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> RateLimitSnapshot:
    if now is None:
        now = datetime.now(timezone.utc)
    return RateLimitSnapshot(
        session_pct=_parse_pct(headers, "anthropic-ratelimit-unified-5h-utilization"),
        session_reset_minutes=_parse_reset_minutes(headers, "anthropic-ratelimit-unified-5h-reset", now=now),
        session_status=_parse_status(headers, "anthropic-ratelimit-unified-5h-status"),
        weekly_pct=_parse_pct(headers, "anthropic-ratelimit-unified-7d-utilization"),
        weekly_reset_minutes=_parse_reset_minutes(headers, "anthropic-ratelimit-unified-7d-reset", now=now),
        weekly_status=_parse_status(headers, "anthropic-ratelimit-unified-7d-status"),
    )
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_anthropic_parse.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/anthropic_client.py tests/test_anthropic_parse.py
git commit -m "feat(anthropic_client): parse ratelimit headers into RateLimitSnapshot"
```

---

## Task 6: anthropic_client — probe()

**Files:**
- Modify: `ccusage_mqtt/anthropic_client.py`
- Test: `tests/test_anthropic_probe.py`

Issues `POST /v1/messages` with the smallest valid body, returns parsed snapshot. Distinguishes recoverable network errors, fatal auth errors, and rate-limited (429) responses.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_anthropic_probe.py
from datetime import datetime, timezone

import pytest
import responses

from ccusage_mqtt.anthropic_client import (
    AnthropicAuthError,
    AnthropicProbeError,
    AnthropicRateLimited,
    probe,
)


BASE = "https://api.anthropic.com"
HEADERS_OK = {
    "anthropic-ratelimit-unified-5h-utilization": "0.30",
    "anthropic-ratelimit-unified-5h-reset": "2026-05-16T15:00:00Z",
    "anthropic-ratelimit-unified-5h-status": "allowed",
    "anthropic-ratelimit-unified-7d-utilization": "0.10",
    "anthropic-ratelimit-unified-7d-reset": "2026-05-23T10:00:00Z",
    "anthropic-ratelimit-unified-7d-status": "allowed",
}


@responses.activate
def test_probe_returns_snapshot_on_200():
    responses.add(
        responses.POST,
        f"{BASE}/v1/messages",
        json={"id": "msg_1", "content": []},
        status=200,
        headers=HEADERS_OK,
    )
    snap = probe(
        api_key="sk-ant-test",
        api_base=BASE,
        model="claude-haiku-4-5-20251001",
        now=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc),
        timeout_sec=5.0,
    )
    assert snap.session_pct == 30.0


@responses.activate
def test_probe_raises_auth_error_on_401():
    responses.add(
        responses.POST,
        f"{BASE}/v1/messages",
        json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}},
        status=401,
    )
    with pytest.raises(AnthropicAuthError):
        probe(api_key="bad", api_base=BASE, model="m", timeout_sec=5.0)


@responses.activate
def test_probe_raises_auth_error_on_403():
    responses.add(responses.POST, f"{BASE}/v1/messages", json={}, status=403)
    with pytest.raises(AnthropicAuthError):
        probe(api_key="bad", api_base=BASE, model="m", timeout_sec=5.0)


@responses.activate
def test_probe_raises_rate_limited_on_429_and_still_parses_headers():
    responses.add(
        responses.POST,
        f"{BASE}/v1/messages",
        json={"error": {"type": "rate_limit_error"}},
        status=429,
        headers={
            **HEADERS_OK,
            "anthropic-ratelimit-unified-5h-utilization": "0.99",
            "anthropic-ratelimit-unified-5h-status": "limited",
        },
    )
    with pytest.raises(AnthropicRateLimited) as exc_info:
        probe(api_key="sk-ant-test", api_base=BASE, model="m",
              now=datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc), timeout_sec=5.0)
    # The exception still carries the snapshot — useful for the publisher.
    assert exc_info.value.snapshot.session_status == "limited"
    assert exc_info.value.snapshot.session_pct == 99.0


@responses.activate
def test_probe_raises_probe_error_on_5xx():
    responses.add(responses.POST, f"{BASE}/v1/messages", json={}, status=503)
    with pytest.raises(AnthropicProbeError):
        probe(api_key="sk-ant-test", api_base=BASE, model="m", timeout_sec=5.0)


@responses.activate
def test_probe_request_body_shape():
    captured = {}
    def callback(request):
        captured["body"] = request.body
        captured["headers"] = dict(request.headers)
        return (200, HEADERS_OK, '{"id":"msg_1","content":[]}')
    responses.add_callback(responses.POST, f"{BASE}/v1/messages", callback=callback)

    probe(api_key="sk-ant-test", api_base=BASE,
          model="claude-haiku-4-5-20251001", timeout_sec=5.0)

    import json
    body = json.loads(captured["body"])
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["max_tokens"] == 1
    assert body["messages"] == [{"role": "user", "content": "."}]
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_anthropic_probe.py -v
```

Expected: `ImportError: cannot import name 'probe' from 'ccusage_mqtt.anthropic_client'` (or similar).

- [ ] **Step 3: Implement `probe()` + exception hierarchy**

Append to `ccusage_mqtt/anthropic_client.py`:

```python
import json as _json

import requests


class AnthropicProbeError(Exception):
    """Recoverable error — caller should keep going."""


class AnthropicAuthError(AnthropicProbeError):
    """Fatal — bad credentials. Caller should exit non-zero."""


class AnthropicRateLimited(AnthropicProbeError):
    """The probe itself was 429'd. Headers may still be present and useful."""
    def __init__(self, snapshot: RateLimitSnapshot) -> None:
        super().__init__("rate limited")
        self.snapshot = snapshot


def probe(
    *,
    api_key: str,
    api_base: str,
    model: str,
    timeout_sec: float,
    now: datetime | None = None,
) -> RateLimitSnapshot:
    """POST /v1/messages with the smallest valid body; return parsed headers.

    Raises:
        AnthropicAuthError: 401 or 403 — credentials are wrong, fatal.
        AnthropicRateLimited: 429 — back off and keep going.
        AnthropicProbeError: any other non-2xx, or network failure.
    """
    url = api_base.rstrip("/") + "/v1/messages"
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "."}],
    }
    request_headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        resp = requests.post(url, headers=request_headers, json=body, timeout=timeout_sec)
    except requests.RequestException as e:
        raise AnthropicProbeError(f"network error: {e}") from e

    if resp.status_code in (401, 403):
        raise AnthropicAuthError(f"{resp.status_code} from {url}: {resp.text[:200]}")

    if resp.status_code == 429:
        snap = parse_ratelimit_headers(resp.headers, now=now)
        raise AnthropicRateLimited(snap)

    if not resp.ok:
        raise AnthropicProbeError(f"{resp.status_code} from {url}: {resp.text[:200]}")

    return parse_ratelimit_headers(resp.headers, now=now)
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_anthropic_probe.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/anthropic_client.py tests/test_anthropic_probe.py
git commit -m "feat(anthropic_client): probe() with auth / rate-limited / recoverable error split"
```

---

## Task 7: ccusage — parse_block JSON

**Files:**
- Create: `ccusage_mqtt/ccusage.py`
- Create: `tests/fixtures/ccusage_active.json`
- Create: `tests/fixtures/ccusage_no_active.json`
- Test: `tests/test_ccusage_parse.py`

Parser is pure. Subprocess wrapper comes in Task 8.

The `ccusage blocks --json` output shape is documented in https://github.com/ryoppippi/ccusage. We parse the active block; if no active block, return `None`. The fixture below reflects ccusage's actual output format as of late 2025. **The implementer should run `ccusage blocks --json` once against a real `~/.claude/projects` directory and verify the shape matches before merging — fix the parser if ccusage's schema has drifted.**

- [ ] **Step 1: Create fixture files**

`tests/fixtures/ccusage_active.json`:

```json
{
  "blocks": [
    {
      "id": "block-1",
      "startTime": "2026-05-16T10:00:00.000Z",
      "endTime": "2026-05-16T15:00:00.000Z",
      "isActive": false,
      "tokenCounts": {"inputTokens": 1000, "outputTokens": 2000, "cacheCreationInputTokens": 0, "cacheReadInputTokens": 0},
      "totalTokens": 3000,
      "costUSD": 0.04,
      "models": ["claude-opus-4-7"]
    },
    {
      "id": "block-2",
      "startTime": "2026-05-16T15:00:00.000Z",
      "endTime": "2026-05-16T20:00:00.000Z",
      "isActive": true,
      "tokenCounts": {"inputTokens": 5000, "outputTokens": 10000, "cacheCreationInputTokens": 1000, "cacheReadInputTokens": 4000},
      "totalTokens": 20000,
      "costUSD": 0.42,
      "models": ["claude-opus-4-7", "claude-haiku-4-5"]
    }
  ]
}
```

`tests/fixtures/ccusage_no_active.json`:

```json
{
  "blocks": [
    {
      "id": "block-1",
      "startTime": "2026-05-16T10:00:00.000Z",
      "endTime": "2026-05-16T15:00:00.000Z",
      "isActive": false,
      "tokenCounts": {"inputTokens": 1000, "outputTokens": 2000, "cacheCreationInputTokens": 0, "cacheReadInputTokens": 0},
      "totalTokens": 3000,
      "costUSD": 0.04,
      "models": ["claude-opus-4-7"]
    }
  ]
}
```

- [ ] **Step 2: Write failing tests**

```python
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
```

- [ ] **Step 3: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ccusage_parse.py -v
```

Expected: `ModuleNotFoundError: No module named 'ccusage_mqtt.ccusage'`.

- [ ] **Step 4: Implement parser**

```python
# ccusage_mqtt/ccusage.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class BlockSnapshot:
    tokens_used: int
    spend_so_far_usd: float
    block_started_at: datetime
    block_ends_at: datetime
    block_elapsed_minutes: float


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_blocks_json(raw: str, *, now: datetime | None = None) -> BlockSnapshot | None:
    """Parse `ccusage blocks --json` stdout. Returns the active block or None.

    Raises ValueError on malformed JSON (subprocess gave us garbage).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    data = json.loads(raw)  # ValueError on malformed
    blocks = data.get("blocks") or []
    active = next((b for b in blocks if b.get("isActive")), None)
    if active is None:
        return None

    tc = active.get("tokenCounts") or {}
    tokens = (
        int(tc.get("inputTokens", 0))
        + int(tc.get("outputTokens", 0))
        + int(tc.get("cacheCreationInputTokens", 0))
        + int(tc.get("cacheReadInputTokens", 0))
    )
    started = _parse_iso(active["startTime"])
    ends = _parse_iso(active["endTime"])
    elapsed_min = max(0.0, (now - started).total_seconds() / 60.0)

    return BlockSnapshot(
        tokens_used=tokens,
        spend_so_far_usd=float(active.get("costUSD", 0.0)),
        block_started_at=started,
        block_ends_at=ends,
        block_elapsed_minutes=elapsed_min,
    )
```

- [ ] **Step 5: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_ccusage_parse.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add ccusage_mqtt/ccusage.py tests/fixtures/ tests/test_ccusage_parse.py
git commit -m "feat(ccusage): parse blocks --json into BlockSnapshot"
```

---

## Task 8: ccusage — subprocess wrapper

**Files:**
- Modify: `ccusage_mqtt/ccusage.py`
- Create: `tests/test_ccusage_run.py`

`run()` shells out to `ccusage`, handles timeouts and non-zero exits, returns a `BlockSnapshot | None`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ccusage_run.py
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from ccusage_mqtt.ccusage import CcusageError, run


SAMPLE_STDOUT = """
{"blocks":[{"id":"b","startTime":"2026-05-16T15:00:00Z","endTime":"2026-05-16T20:00:00Z","isActive":true,"tokenCounts":{"inputTokens":100,"outputTokens":200,"cacheCreationInputTokens":0,"cacheReadInputTokens":0},"costUSD":0.01,"models":[]}]}
""".strip()


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_invokes_ccusage_with_correct_args(mock_subprocess):
    mock_subprocess.return_value = MagicMock(returncode=0, stdout=SAMPLE_STDOUT, stderr="")
    snap = run(projects_dir="/data/claude-projects", timeout_sec=10.0,
              now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))
    assert snap is not None
    assert snap.tokens_used == 300

    args = mock_subprocess.call_args
    cmd = args.kwargs.get("args") or args.args[0]
    assert cmd[:3] == ["npx", "ccusage", "blocks"]
    assert "--json" in cmd
    assert "--offline" in cmd
    env = args.kwargs.get("env") or {}
    assert env.get("CLAUDE_CONFIG_DIR") == "/data/claude-projects"


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_raises_on_nonzero_exit(mock_subprocess):
    mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(CcusageError, match="exit code 1"):
        run(projects_dir="/data/claude-projects", timeout_sec=10.0)


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_raises_on_garbage_stdout(mock_subprocess):
    mock_subprocess.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
    with pytest.raises(CcusageError, match="malformed JSON"):
        run(projects_dir="/data/claude-projects", timeout_sec=10.0)


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_raises_on_timeout(mock_subprocess):
    import subprocess as sp
    mock_subprocess.side_effect = sp.TimeoutExpired(cmd="ccusage", timeout=10.0)
    with pytest.raises(CcusageError, match="timed out"):
        run(projects_dir="/data/claude-projects", timeout_sec=10.0)
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_ccusage_run.py -v
```

Expected: `ImportError: cannot import name 'run' from 'ccusage_mqtt.ccusage'`.

- [ ] **Step 3: Add subprocess wrapper to `ccusage.py`**

Append to `ccusage_mqtt/ccusage.py`:

```python
import os
import subprocess


class CcusageError(Exception):
    """Recoverable — caller should keep last-known token/spend values."""


def run(
    *,
    projects_dir: str,
    timeout_sec: float,
    now: datetime | None = None,
) -> BlockSnapshot | None:
    """Invoke `npx ccusage blocks --json --offline` against projects_dir.

    Returns the active block snapshot, or None if no active block.
    Raises CcusageError on subprocess failure or malformed output.
    """
    env = {**os.environ, "CLAUDE_CONFIG_DIR": projects_dir}
    try:
        result = subprocess.run(
            args=["npx", "ccusage", "blocks", "--json", "--offline"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CcusageError(f"ccusage timed out after {timeout_sec}s") from e
    if result.returncode != 0:
        raise CcusageError(f"ccusage exit code {result.returncode}: {result.stderr[:200]}")
    try:
        return parse_blocks_json(result.stdout, now=now)
    except ValueError as e:
        raise CcusageError(f"ccusage produced malformed JSON: {e}") from e
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_ccusage_run.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/ccusage.py tests/test_ccusage_run.py
git commit -m "feat(ccusage): run() subprocess wrapper with timeout + error mapping"
```

---

## Task 9: state — State dataclass + apply methods

**Files:**
- Create: `ccusage_mqtt/state.py`
- Test: `tests/test_state.py`

`State` is a mutable container. `apply_rate_limits()` writes the 6 header-derived fields. `apply_block()` writes the 2 ccusage fields plus stash the block timing for derivations.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_state.py
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `State`**

```python
# ccusage_mqtt/state.py
from __future__ import annotations

from dataclasses import dataclass, field

from ccusage_mqtt.anthropic_client import RateLimitSnapshot, Status
from ccusage_mqtt.ccusage import BlockSnapshot
from ccusage_mqtt.usage_rate import Mood


@dataclass
class State:
    session_pct: float | None = None
    session_reset_minutes: int | None = None
    session_status: Status = "unknown"
    weekly_pct: float | None = None
    weekly_reset_minutes: int | None = None
    weekly_status: Status = "unknown"

    burn_rate_pct_per_min: float | None = None
    mood: Mood = "idle"
    time_to_limit_minutes: float | None = None
    block_elapsed_pct: float | None = None

    tokens_used: int | None = None
    tokens_per_hour: float | None = None
    spend_so_far_usd: float | None = None
    spend_per_hour_usd: float | None = None

    # Internal — not published.
    block_elapsed_minutes: float | None = field(default=None, repr=False)

    def apply_rate_limits(self, snap: RateLimitSnapshot) -> None:
        self.session_pct = snap.session_pct
        self.session_reset_minutes = snap.session_reset_minutes
        self.session_status = snap.session_status
        self.weekly_pct = snap.weekly_pct
        self.weekly_reset_minutes = snap.weekly_reset_minutes
        self.weekly_status = snap.weekly_status

    def apply_block(self, snap: BlockSnapshot) -> None:
        self.tokens_used = snap.tokens_used
        self.spend_so_far_usd = snap.spend_so_far_usd
        self.block_elapsed_minutes = snap.block_elapsed_minutes

    def mark_headers_stale(self) -> None:
        self.session_status = "unknown"
        self.weekly_status = "unknown"
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/state.py tests/test_state.py
git commit -m "feat(state): State dataclass + apply_rate_limits/apply_block/mark_headers_stale"
```

---

## Task 10: state — derived sensors

**Files:**
- Modify: `ccusage_mqtt/state.py`
- Modify: `tests/test_state.py`

Adds `recompute_derived()` that fills `burn_rate_pct_per_min`, `mood`, `time_to_limit_minutes`, `block_elapsed_pct`, `tokens_per_hour`, `spend_per_hour_usd`.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_state.py`:

```python
def test_recompute_derived_with_full_data():
    s = State()
    s.session_pct = 50.0
    s.session_reset_minutes = 180  # 2h left of 5h block → 3h elapsed = 60% elapsed
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```

Expected: 4 new tests fail.

- [ ] **Step 3: Implement `recompute_derived`**

Two edits to `ccusage_mqtt/state.py`:

(a) Add a `DerivationConfig` dataclass and module constants at the top of the file (after the existing `from ccusage_mqtt.usage_rate import Mood` import, add `classify_mood` to that import):

```python
from ccusage_mqtt.usage_rate import Mood, classify_mood

BLOCK_WINDOW_MINUTES = 300.0  # Anthropic 5h window
RATE_MIN_ELAPSED_MIN = 1.0    # need ≥1 min of block before publishing per-hour rates


@dataclass
class DerivationConfig:
    idle_below: float = 0.10
    normal_below: float = 0.20
    active_below: float = 0.33
```

(b) Add `recompute_derived` as a method inside the `State` class body (immediately after `mark_headers_stale`):

```python
    def recompute_derived(
        self,
        *,
        burn_rate: float | None,
        cfg: DerivationConfig | None = None,
    ) -> None:
        cfg = cfg or DerivationConfig()
        self.burn_rate_pct_per_min = burn_rate
        self.mood = classify_mood(
            burn_rate,
            idle_below=cfg.idle_below,
            normal_below=cfg.normal_below,
            active_below=cfg.active_below,
        )

        if burn_rate is not None and burn_rate > 0.0 and self.session_pct is not None:
            self.time_to_limit_minutes = (100.0 - self.session_pct) / burn_rate
        else:
            self.time_to_limit_minutes = None

        if self.session_reset_minutes is not None:
            elapsed = BLOCK_WINDOW_MINUTES - self.session_reset_minutes
            self.block_elapsed_pct = max(0.0, min(100.0, elapsed / BLOCK_WINDOW_MINUTES * 100.0))
        else:
            self.block_elapsed_pct = None

        if (
            self.block_elapsed_minutes is not None
            and self.block_elapsed_minutes >= RATE_MIN_ELAPSED_MIN
        ):
            elapsed_h = self.block_elapsed_minutes / 60.0
            self.tokens_per_hour = (self.tokens_used / elapsed_h) if self.tokens_used is not None else None
            self.spend_per_hour_usd = (self.spend_so_far_usd / elapsed_h) if self.spend_so_far_usd is not None else None
        else:
            self.tokens_per_hour = None
            self.spend_per_hour_usd = None
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```

Expected: 8 passed total.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/state.py tests/test_state.py
git commit -m "feat(state): recompute_derived (burn_rate, mood, time_to_limit, %elapsed, hourly rates)"
```

---

## Task 11: state — to_mqtt_payloads

**Files:**
- Modify: `ccusage_mqtt/state.py`
- Modify: `tests/test_state.py`

Returns `dict[sensor_id, dict]` where each value is the JSON payload `{"value": <num | str | None>}` ready to publish.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_state.py`:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```

Expected: 2 new tests fail.

- [ ] **Step 3: Implement `to_mqtt_payloads`**

Two edits to `ccusage_mqtt/state.py`:

(a) Add `SENSOR_FIELDS` as a module-level constant (after `DerivationConfig`):

```python
SENSOR_FIELDS: tuple[str, ...] = (
    "session_pct",
    "session_reset_minutes",
    "session_status",
    "weekly_pct",
    "weekly_reset_minutes",
    "weekly_status",
    "burn_rate_pct_per_min",
    "mood",
    "time_to_limit_minutes",
    "block_elapsed_pct",
    "tokens_used",
    "tokens_per_hour",
    "spend_so_far_usd",
    "spend_per_hour_usd",
)
```

(b) Add `to_mqtt_payloads` as a method inside the `State` class body (immediately after `recompute_derived`):

```python
    def to_mqtt_payloads(self) -> dict[str, dict]:
        return {name: {"value": getattr(self, name)} for name in SENSOR_FIELDS}
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```

Expected: 10 passed total.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/state.py tests/test_state.py
git commit -m "feat(state): to_mqtt_payloads serialization for all 14 sensors"
```

---

## Task 12: publisher — HA discovery configs

**Files:**
- Create: `ccusage_mqtt/publisher.py`
- Test: `tests/test_publisher_discovery.py`

A pure function that builds the 14 HA discovery JSON configs. The publisher will send each to `homeassistant/sensor/claude_code_usage/<id>/config` with retain at startup.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_publisher_discovery.py
import json

from ccusage_mqtt.publisher import DiscoveryConfig, build_discovery_configs


def test_returns_14_configs():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    assert len(cfgs) == 14


def test_each_config_has_required_ha_fields():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    for cfg in cfgs:
        assert isinstance(cfg, DiscoveryConfig)
        body = json.loads(cfg.payload)
        assert body["unique_id"].startswith("claude_code_usage_")
        assert body["state_topic"].startswith("claude_code_usage/")
        assert body["state_topic"].endswith("/state")
        assert body["value_template"] == "{{ value_json.value }}"
        assert body["availability_topic"] == "claude_code_usage/availability"
        assert body["payload_available"] == "online"
        assert body["payload_not_available"] == "offline"
        assert body["device"]["identifiers"] == ["claude_code_usage"]
        assert body["device"]["name"] == "Claude Code Usage"


def test_mood_sensor_has_enum_options():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    mood = next(c for c in cfgs if c.sensor_id == "mood")
    body = json.loads(mood.payload)
    assert body["device_class"] == "enum"
    assert set(body["options"]) == {"idle", "normal", "active", "heavy"}


def test_session_pct_sensor_has_percent_unit():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    spct = next(c for c in cfgs if c.sensor_id == "session_pct")
    body = json.loads(spct.payload)
    assert body["unit_of_measurement"] == "%"
    assert body["state_class"] == "measurement"


def test_discovery_topic_format():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
        discovery_prefix="homeassistant",
    )
    spct = next(c for c in cfgs if c.sensor_id == "session_pct")
    assert spct.topic == "homeassistant/sensor/claude_code_usage/session_pct/config"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_publisher_discovery.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement discovery builder**

```python
# ccusage_mqtt/publisher.py
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryConfig:
    sensor_id: str
    topic: str
    payload: str  # JSON-serialized


# (sensor_id, friendly_name, unit, device_class, state_class, icon, [enum options or None])
_SENSOR_SPECS: tuple[tuple[str, str, str | None, str | None, str | None, str, list[str] | None], ...] = (
    ("session_pct",            "Session %",            "%",        None,        "measurement",     "mdi:gauge",                None),
    ("session_reset_minutes",  "Session resets in",    "min",      None,        "measurement",     "mdi:timer-sand",           None),
    ("session_status",         "Session status",       None,       "enum",      None,              "mdi:traffic-light",        ["allowed", "limited", "unknown"]),
    ("weekly_pct",             "Weekly %",             "%",        None,        "measurement",     "mdi:gauge",                None),
    ("weekly_reset_minutes",   "Weekly resets in",     "min",      None,        "measurement",     "mdi:timer-sand",           None),
    ("weekly_status",          "Weekly status",        None,       "enum",      None,              "mdi:traffic-light",        ["allowed", "limited", "unknown"]),
    ("burn_rate_pct_per_min",  "Burn rate",            "%/min",    None,        "measurement",     "mdi:chart-line",           None),
    ("mood",                   "Mood",                 None,       "enum",      None,              "mdi:emoticon",             ["idle", "normal", "active", "heavy"]),
    ("time_to_limit_minutes",  "Time to limit",        "min",      None,        "measurement",     "mdi:timer-sand-complete",  None),
    ("block_elapsed_pct",      "Block elapsed",        "%",        None,        "measurement",     "mdi:progress-clock",       None),
    ("tokens_used",            "Tokens used",          "tokens",   None,        "total_increasing","mdi:format-letter-matches", None),
    ("tokens_per_hour",        "Tokens per hour",      "tokens/h", None,        "measurement",     "mdi:speedometer",          None),
    ("spend_so_far_usd",       "Spend so far",         "USD",      "monetary",  "total_increasing","mdi:currency-usd",         None),
    ("spend_per_hour_usd",     "Spend per hour",       "USD/h",    None,        "measurement",     "mdi:cash-clock",           None),
)


def build_discovery_configs(
    *,
    device_id: str,
    device_name: str,
    base_topic: str,
    discovery_prefix: str = "homeassistant",
) -> list[DiscoveryConfig]:
    device_block = {
        "identifiers": [device_id],
        "name": device_name,
        "manufacturer": "ccusage-mqtt",
        "model": "ccusage-mqtt",
    }
    configs: list[DiscoveryConfig] = []
    for sid, fname, unit, dclass, sclass, icon, options in _SENSOR_SPECS:
        body: dict = {
            "name": fname,
            "unique_id": f"{device_id}_{sid}",
            "object_id": f"{device_id}_{sid}",
            "state_topic": f"{base_topic}/{sid}/state",
            "value_template": "{{ value_json.value }}",
            "availability_topic": f"{base_topic}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "icon": icon,
            "device": device_block,
        }
        if unit is not None:
            body["unit_of_measurement"] = unit
        if dclass is not None:
            body["device_class"] = dclass
        if sclass is not None:
            body["state_class"] = sclass
        if options is not None:
            body["options"] = options
        configs.append(DiscoveryConfig(
            sensor_id=sid,
            topic=f"{discovery_prefix}/sensor/{device_id}/{sid}/config",
            payload=json.dumps(body),
        ))
    return configs
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_publisher_discovery.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/publisher.py tests/test_publisher_discovery.py
git commit -m "feat(publisher): build_discovery_configs for all 14 HA sensors"
```

---

## Task 13: publisher — MQTT client wrapper

**Files:**
- Modify: `ccusage_mqtt/publisher.py`
- Test: `tests/test_publisher_client.py`

`MqttClient` wraps `paho.mqtt.client.Client`, sets LWT, publishes discovery + state with retain, handles reconnect by re-publishing discovery + availability.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_publisher_client.py
import json
from unittest.mock import MagicMock, patch

from ccusage_mqtt.publisher import MqttClient, build_discovery_configs


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_client_sets_will_on_construct(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake

    MqttClient(
        host="broker", port=1883, username=None, password=None,
        client_id="ccusage-mqtt",
        availability_topic="claude_code_usage/availability",
    )

    fake.will_set.assert_called_once_with(
        "claude_code_usage/availability",
        payload="offline",
        qos=1,
        retain=True,
    )


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_publish_discovery_sends_each_config_retained(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="claude_code_usage/availability")

    cfgs = build_discovery_configs(
        device_id="claude_code_usage", device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    client.publish_discovery(cfgs)

    assert fake.publish.call_count == 14
    # Spot-check one call
    call0 = fake.publish.call_args_list[0]
    assert call0.kwargs.get("retain", call0.args[3] if len(call0.args) >= 4 else None) is True


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_publish_state_sends_json_envelope(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="a")

    client.publish_state(base_topic="claude_code_usage",
                         payloads={"session_pct": {"value": 42.0}, "mood": {"value": "idle"}})

    assert fake.publish.call_count == 2
    topics = sorted(c.args[0] for c in fake.publish.call_args_list)
    assert topics == ["claude_code_usage/mood/state", "claude_code_usage/session_pct/state"]
    payloads = sorted(c.args[1] for c in fake.publish.call_args_list)
    assert json.loads(payloads[0]) == {"value": "idle"}
    assert json.loads(payloads[1]) == {"value": 42.0}


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_publish_state_skips_unchanged_values_on_repeat(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="a")

    client.publish_state(base_topic="ct", payloads={"a": {"value": 1}, "b": {"value": 2}})
    fake.publish.reset_mock()
    client.publish_state(base_topic="ct", payloads={"a": {"value": 1}, "b": {"value": 3}})

    # Only 'b' changed
    assert fake.publish.call_count == 1
    assert fake.publish.call_args.args[0] == "ct/b/state"


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_on_connect_publishes_online_and_rediscovers(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="claude_code_usage/availability")
    cfgs = build_discovery_configs(
        device_id="claude_code_usage", device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    client.set_discovery_configs(cfgs)

    # Simulate paho-mqtt invoking the on_connect callback (signature differs by paho version;
    # we use Callback API v2 → on_connect(client, userdata, flags, reason_code, properties))
    client._on_connect(fake, None, {}, 0, None)

    # Should have: 1 availability=online + 14 discovery publishes
    assert fake.publish.call_count == 15
    topics = [c.args[0] for c in fake.publish.call_args_list]
    assert topics[0] == "claude_code_usage/availability"
    discovery_topics = topics[1:]
    assert all(t.startswith("homeassistant/sensor/claude_code_usage/") for t in discovery_topics)
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_publisher_client.py -v
```

Expected: `ImportError` for `MqttClient`.

- [ ] **Step 3: Implement `MqttClient`**

Append to `ccusage_mqtt/publisher.py`:

```python
import logging
from typing import Iterable

import paho.mqtt.client as mqtt

_log = logging.getLogger(__name__)


class MqttClient:
    """Wraps paho-mqtt with LWT, retained publishing, and re-discovery on reconnect."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        client_id: str,
        availability_topic: str,
    ) -> None:
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=False,
        )
        if username:
            self._client.username_pw_set(username, password or "")
        self._client.will_set(availability_topic, payload="offline", qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        self._host = host
        self._port = port
        self._availability_topic = availability_topic
        self._discovery: list[DiscoveryConfig] = []
        self._last_state: dict[str, dict] = {}

    def set_discovery_configs(self, configs: list[DiscoveryConfig]) -> None:
        self._discovery = list(configs)

    def connect_and_loop(self) -> None:
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        # Cleanly publish offline; paho may also fire LWT on disconnect.
        try:
            self._client.publish(self._availability_topic, payload="offline", qos=1, retain=True)
        finally:
            self._client.loop_stop()
            self._client.disconnect()

    def publish_discovery(self, configs: Iterable[DiscoveryConfig]) -> None:
        for cfg in configs:
            self._client.publish(cfg.topic, cfg.payload, qos=1, retain=True)

    def publish_state(self, *, base_topic: str, payloads: dict[str, dict]) -> None:
        import json
        for sensor_id, payload in payloads.items():
            if self._last_state.get(sensor_id) == payload:
                continue
            self._client.publish(
                f"{base_topic}/{sensor_id}/state",
                json.dumps(payload),
                qos=1,
                retain=True,
            )
            self._last_state[sensor_id] = payload

    # --- paho callbacks ---

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            _log.warning("mqtt connect failed: reason_code=%s", reason_code)
            return
        _log.info("mqtt connected to %s:%s", self._host, self._port)
        client.publish(self._availability_topic, payload="online", qos=1, retain=True)
        # Re-publish discovery so HA re-creates entities if they got cleared.
        for cfg in self._discovery:
            client.publish(cfg.topic, cfg.payload, qos=1, retain=True)
        # Force re-publish of state on next publish_state() by clearing the dedup cache.
        self._last_state.clear()

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        _log.warning("mqtt disconnected: reason_code=%s — paho will auto-reconnect", reason_code)
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_publisher_client.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/publisher.py tests/test_publisher_client.py
git commit -m "feat(publisher): MqttClient wrapper with LWT, retained publish, re-discovery on reconnect"
```

---

## Task 14: publisher — main loop

**Files:**
- Modify: `ccusage_mqtt/publisher.py`
- Test: `tests/test_publisher_loop.py`

The main loop is a class `PublisherLoop` that owns the State, the ring buffer, both pollers (injected as callables for testability), and the MQTT client. One iteration is exposed as `tick(now)` so tests can drive it.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_publisher_loop.py
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
    assert payloads["session_pct"] == {"value": 30.0}
    assert payloads["session_status"] == {"value": "allowed"}
    assert payloads["mood"] == {"value": "idle"}  # warm-up — only 1 sample
    assert payloads["burn_rate_pct_per_min"] == {"value": None}


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
    assert final["burn_rate_pct_per_min"] == {"value": pytest.approx(1.0)}
    assert final["mood"] == {"value": "heavy"}


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
    assert final["session_status"] == {"value": "unknown"}


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
    assert final["session_pct"] == {"value": 30.0}  # header still flows
    assert final["tokens_used"] == {"value": None}   # ccusage missing


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
    assert final["session_status"] == {"value": "limited"}
    assert final["session_pct"] == {"value": 99.0}
```

Add this import at the top of `tests/test_publisher_loop.py`:

```python
import pytest
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_publisher_loop.py -v
```

Expected: `ImportError: cannot import name 'PublisherLoop'`.

- [ ] **Step 3: Implement `PublisherLoop`**

Append to `ccusage_mqtt/publisher.py`:

```python
from dataclasses import dataclass
from typing import Callable

from ccusage_mqtt.anthropic_client import (
    AnthropicAuthError,
    AnthropicProbeError,
    AnthropicRateLimited,
    RateLimitSnapshot,
)
from ccusage_mqtt.ccusage import BlockSnapshot, CcusageError
from ccusage_mqtt.state import DerivationConfig, State
from ccusage_mqtt.usage_rate import RingBuffer, compute_rate, detect_reset


_HEADER_BACKOFF_AFTER_429_SEC = 300.0
_HEADERS_STALE_AFTER_FAILURES = 3


@dataclass
class LoopConfig:
    base_topic: str
    header_poll_sec: float
    ccusage_poll_sec: float
    burn_rate_window_sec: float
    idle_below: float
    normal_below: float
    active_below: float


class PublisherLoop:
    def __init__(
        self,
        *,
        cfg: LoopConfig,
        mqtt: "MqttClient",
        poll_headers: Callable[[], RateLimitSnapshot],
        poll_ccusage: Callable[[], BlockSnapshot | None],
    ) -> None:
        self._cfg = cfg
        self._mqtt = mqtt
        self._poll_headers = poll_headers
        self._poll_ccusage = poll_ccusage

        self._state = State()
        self._ring = RingBuffer(capacity=6)
        self._last_header_at: float | None = None
        self._last_ccusage_at: float | None = None
        self._next_header_due: float = 0.0
        self._next_ccusage_due: float = 0.0
        self._header_failure_streak = 0

    def tick(self, *, now_monotonic: float) -> None:
        if now_monotonic >= self._next_header_due:
            self._do_header_poll(now_monotonic)
        if now_monotonic >= self._next_ccusage_due:
            self._do_ccusage_poll(now_monotonic)

        burn_rate = compute_rate(self._ring, min_window_sec=self._cfg.burn_rate_window_sec)
        self._state.recompute_derived(
            burn_rate=burn_rate,
            cfg=DerivationConfig(
                idle_below=self._cfg.idle_below,
                normal_below=self._cfg.normal_below,
                active_below=self._cfg.active_below,
            ),
        )
        self._mqtt.publish_state(
            base_topic=self._cfg.base_topic,
            payloads=self._state.to_mqtt_payloads(),
        )

    def _do_header_poll(self, now_monotonic: float) -> None:
        try:
            snap = self._poll_headers()
            self._apply_header_snap(snap, now_monotonic)
            self._header_failure_streak = 0
            self._next_header_due = now_monotonic + self._cfg.header_poll_sec
        except AnthropicAuthError:
            raise  # fatal — propagate to __main__
        except AnthropicRateLimited as e:
            self._apply_header_snap(e.snapshot, now_monotonic)
            self._header_failure_streak = 0
            self._next_header_due = now_monotonic + _HEADER_BACKOFF_AFTER_429_SEC
        except AnthropicProbeError:
            self._header_failure_streak += 1
            if self._header_failure_streak >= _HEADERS_STALE_AFTER_FAILURES:
                self._state.mark_headers_stale()
            self._next_header_due = now_monotonic + self._cfg.header_poll_sec

    def _apply_header_snap(self, snap: RateLimitSnapshot, now_monotonic: float) -> None:
        if snap.session_pct is not None:
            if detect_reset(self._ring, new_pct=snap.session_pct):
                self._ring.clear()
            self._ring.add(now_monotonic, snap.session_pct)
        self._state.apply_rate_limits(snap)

    def _do_ccusage_poll(self, now_monotonic: float) -> None:
        try:
            snap = self._poll_ccusage()
            if snap is not None:
                self._state.apply_block(snap)
            self._next_ccusage_due = now_monotonic + self._cfg.ccusage_poll_sec
        except CcusageError:
            # Retained token/spend values stand.
            self._next_ccusage_due = now_monotonic + self._cfg.ccusage_poll_sec
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_publisher_loop.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add ccusage_mqtt/publisher.py tests/test_publisher_loop.py
git commit -m "feat(publisher): PublisherLoop with two-poller scheduling, failure handling, rate-limit backoff"
```

---

## Task 15: __main__ — entry point, config, logging, signals

**Files:**
- Create: `ccusage_mqtt/__main__.py`
- Test: `tests/test_main_config.py`

Reads env vars, builds `LoopConfig` + `MqttClient` + pollers, runs the loop, handles SIGINT/SIGTERM cleanly. The config loader is testable as a pure function.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_main_config.py
import pytest

from ccusage_mqtt.__main__ import AppConfig, load_config_from_env


def test_load_config_from_env_full():
    env = {
        "MQTT_HOST": "10.0.0.1",
        "MQTT_PORT": "8883",
        "MQTT_USER": "u",
        "MQTT_PASS": "p",
        "MQTT_CLIENT_ID": "test-client",
        "MQTT_DISCOVERY_PREFIX": "homeassistant",
        "MQTT_BASE_TOPIC": "claude_code_usage",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_API_BASE": "https://api.anthropic.com",
        "PROBE_MODEL": "claude-haiku-4-5-20251001",
        "CCUSAGE_PROJECTS_DIR": "/data/claude-projects",
        "HEADER_POLL_SEC": "60",
        "CCUSAGE_POLL_SEC": "30",
        "BURN_RATE_WINDOW_SEC": "240",
        "MOOD_IDLE_BELOW": "0.10",
        "MOOD_NORMAL_BELOW": "0.20",
        "MOOD_ACTIVE_BELOW": "0.33",
        "LOG_LEVEL": "DEBUG",
    }
    cfg = load_config_from_env(env)
    assert cfg.mqtt_host == "10.0.0.1"
    assert cfg.mqtt_port == 8883
    assert cfg.mqtt_user == "u"
    assert cfg.anthropic_api_key == "sk-ant-test"
    assert cfg.header_poll_sec == 60.0
    assert cfg.mood_active_below == 0.33
    assert cfg.log_level == "DEBUG"


def test_load_config_uses_defaults():
    env = {
        "MQTT_HOST": "broker",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    cfg = load_config_from_env(env)
    assert cfg.mqtt_port == 1883
    assert cfg.mqtt_discovery_prefix == "homeassistant"
    assert cfg.mqtt_base_topic == "claude_code_usage"
    assert cfg.header_poll_sec == 60.0
    assert cfg.ccusage_poll_sec == 30.0
    assert cfg.burn_rate_window_sec == 240.0
    assert cfg.mood_idle_below == 0.10
    assert cfg.log_level == "INFO"


def test_load_config_requires_mqtt_host():
    with pytest.raises(SystemExit, match="MQTT_HOST"):
        load_config_from_env({"ANTHROPIC_API_KEY": "sk"})


def test_load_config_requires_api_key():
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        load_config_from_env({"MQTT_HOST": "broker"})
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
.venv/bin/python -m pytest tests/test_main_config.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `__main__.py`**

```python
# ccusage_mqtt/__main__.py
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Mapping

from ccusage_mqtt.anthropic_client import AnthropicAuthError, probe
from ccusage_mqtt.ccusage import run as ccusage_run
from ccusage_mqtt.publisher import (
    LoopConfig,
    MqttClient,
    PublisherLoop,
    build_discovery_configs,
)


@dataclass(frozen=True)
class AppConfig:
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str | None
    mqtt_pass: str | None
    mqtt_client_id: str
    mqtt_discovery_prefix: str
    mqtt_base_topic: str

    anthropic_api_key: str
    anthropic_api_base: str
    probe_model: str

    ccusage_projects_dir: str

    header_poll_sec: float
    ccusage_poll_sec: float
    burn_rate_window_sec: float
    mood_idle_below: float
    mood_normal_below: float
    mood_active_below: float

    log_level: str


def _required(env: Mapping[str, str], name: str) -> str:
    val = env.get(name)
    if not val:
        raise SystemExit(f"missing required env var: {name}")
    return val


def load_config_from_env(env: Mapping[str, str]) -> AppConfig:
    return AppConfig(
        mqtt_host=_required(env, "MQTT_HOST"),
        mqtt_port=int(env.get("MQTT_PORT", "1883")),
        mqtt_user=env.get("MQTT_USER") or None,
        mqtt_pass=env.get("MQTT_PASS") or None,
        mqtt_client_id=env.get("MQTT_CLIENT_ID", "ccusage-mqtt"),
        mqtt_discovery_prefix=env.get("MQTT_DISCOVERY_PREFIX", "homeassistant"),
        mqtt_base_topic=env.get("MQTT_BASE_TOPIC", "claude_code_usage"),
        anthropic_api_key=_required(env, "ANTHROPIC_API_KEY"),
        anthropic_api_base=env.get("ANTHROPIC_API_BASE", "https://api.anthropic.com"),
        probe_model=env.get("PROBE_MODEL", "claude-haiku-4-5-20251001"),
        ccusage_projects_dir=env.get("CCUSAGE_PROJECTS_DIR", "/data/claude-projects"),
        header_poll_sec=float(env.get("HEADER_POLL_SEC", "60")),
        ccusage_poll_sec=float(env.get("CCUSAGE_POLL_SEC", "30")),
        burn_rate_window_sec=float(env.get("BURN_RATE_WINDOW_SEC", "240")),
        mood_idle_below=float(env.get("MOOD_IDLE_BELOW", "0.10")),
        mood_normal_below=float(env.get("MOOD_NORMAL_BELOW", "0.20")),
        mood_active_below=float(env.get("MOOD_ACTIVE_BELOW", "0.33")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    cfg = load_config_from_env(os.environ)
    _setup_logging(cfg.log_level)
    log = logging.getLogger("ccusage_mqtt")
    log.info("starting ccusage-mqtt (host=%s port=%s)", cfg.mqtt_host, cfg.mqtt_port)

    discovery = build_discovery_configs(
        device_id=cfg.mqtt_base_topic,
        device_name="Claude Code Usage",
        base_topic=cfg.mqtt_base_topic,
        discovery_prefix=cfg.mqtt_discovery_prefix,
    )

    mqtt_client = MqttClient(
        host=cfg.mqtt_host,
        port=cfg.mqtt_port,
        username=cfg.mqtt_user,
        password=cfg.mqtt_pass,
        client_id=cfg.mqtt_client_id,
        availability_topic=f"{cfg.mqtt_base_topic}/availability",
    )
    mqtt_client.set_discovery_configs(discovery)
    mqtt_client.connect_and_loop()
    # Belt-and-suspenders: publish discovery now too (on_connect also does it
    # asynchronously when the broker handshake completes).
    mqtt_client.publish_discovery(discovery)

    def poll_headers():
        return probe(
            api_key=cfg.anthropic_api_key,
            api_base=cfg.anthropic_api_base,
            model=cfg.probe_model,
            timeout_sec=10.0,
        )

    def poll_ccusage():
        return ccusage_run(
            projects_dir=cfg.ccusage_projects_dir,
            timeout_sec=15.0,
        )

    loop = PublisherLoop(
        cfg=LoopConfig(
            base_topic=cfg.mqtt_base_topic,
            header_poll_sec=cfg.header_poll_sec,
            ccusage_poll_sec=cfg.ccusage_poll_sec,
            burn_rate_window_sec=cfg.burn_rate_window_sec,
            idle_below=cfg.mood_idle_below,
            normal_below=cfg.mood_normal_below,
            active_below=cfg.mood_active_below,
        ),
        mqtt=mqtt_client,
        poll_headers=poll_headers,
        poll_ccusage=poll_ccusage,
    )

    shutdown = False
    def _stop(signum, frame):
        nonlocal shutdown
        log.info("received signal %s, shutting down", signum)
        shutdown = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while not shutdown:
            try:
                loop.tick(now_monotonic=time.monotonic())
            except AnthropicAuthError as e:
                log.error("anthropic auth error — exiting: %s", e)
                return 2
            time.sleep(min(5.0, cfg.ccusage_poll_sec / 2.0))
    finally:
        mqtt_client.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
.venv/bin/python -m pytest tests/test_main_config.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests across all 15 files pass.

- [ ] **Step 6: Commit**

```bash
git add ccusage_mqtt/__main__.py tests/test_main_config.py
git commit -m "feat(main): env config loader + signal-safe main loop entry point"
```

---

## Task 16: Dockerfile + docker-compose.yml

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Modify: `.gitignore` (already exists from spec commit)

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
FROM node:22-alpine

RUN apk add --no-cache python3 py3-pip

# Pin ccusage version. Bump deliberately.
RUN npm install -g ccusage@16.2.6

WORKDIR /app
COPY pyproject.toml ./
COPY ccusage_mqtt ./ccusage_mqtt

RUN pip install --break-system-packages --no-cache-dir .

# Run as non-root for defense in depth — uid is arbitrary, doesn't need to
# match the host's claude user since the mount is read-only.
RUN adduser -D -u 10001 ccusage
USER ccusage

ENTRYPOINT ["python3", "-m", "ccusage_mqtt"]
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  ccusage-mqtt:
    build: .
    image: ccusage-mqtt:local
    container_name: ccusage-mqtt
    restart: unless-stopped
    env_file: .env
    volumes:
      - ${HOME}/.claude:/data/claude-projects:ro
    network_mode: host
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 3: Verify the image builds**

```bash
docker compose build
```

Expected: build succeeds; final image tag `ccusage-mqtt:local`.

- [ ] **Step 4: Smoke-test the image without bringing up the service**

```bash
docker run --rm -e MQTT_HOST=test -e ANTHROPIC_API_KEY=test ccusage-mqtt:local --help 2>&1 | head -3 || true
docker run --rm -e MQTT_HOST=test -e ANTHROPIC_API_KEY=test ccusage-mqtt:local python3 -c "import ccusage_mqtt.publisher; print('import ok')"
```

Expected: second command prints `import ok`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "feat(docker): node:22-alpine + python image + compose with HOME/.claude bind-mount"
```

---

## Task 17: Live deployment + acceptance smoke test

**Files:**
- Modify: `README.md` (add acceptance checklist + troubleshooting)

This task brings the container up against the real broker and verifies HA shows the device. It is not pure code — it's the end-to-end test the spec demands.

- [ ] **Step 1: Populate `.env` from the real broker creds**

```bash
cp .env.example .env
chmod 600 .env
# Fill in MQTT_HOST, MQTT_USER, MQTT_PASS for your broker.
# Fill in ANTHROPIC_API_KEY (use the same key Claude Code uses on this host).
$EDITOR .env
```

- [ ] **Step 2: Bring the service up**

```bash
docker compose up -d --build
docker compose logs -f --tail=50
```

Expected log lines (first 60 seconds):
- `starting ccusage-mqtt (host=<your-broker> port=1883)`
- `mqtt connected to <your-broker>:1883`
- (after first header poll) state updates with session_pct values.

- [ ] **Step 3: Verify HA discovered the device**

In Home Assistant: **Settings → Devices & Services → MQTT → Devices** — a `Claude Code Usage` device should appear with 14 entities (`sensor.claude_code_usage_session_pct`, `sensor.claude_code_usage_mood`, etc.).

Within 60 seconds, expect:
- `session_pct`, `weekly_pct`, `session_status`, `weekly_status` show real values
- `mood` shows `idle` (warm-up state)
- `tokens_used`, `spend_so_far_usd` show real values if ccusage found an active block

Wait 4 minutes. Then:
- `burn_rate_pct_per_min` should show a non-null number
- `mood` should re-classify based on the burn rate

- [ ] **Step 4: Verify availability LWT**

```bash
docker compose kill ccusage-mqtt
```

In HA, all 14 sensors should go `unavailable` within 30 seconds.

```bash
docker compose up -d
```

Sensors return online within 60 seconds with retained values restored from the broker.

- [ ] **Step 5: Update README with acceptance checklist**

Append to `README.md`:

```markdown
## Acceptance verification

After `docker compose up -d --build`, confirm:

- [ ] `docker compose logs` shows `mqtt connected to <host>:<port>` and successful first poll within 60s.
- [ ] HA → MQTT → Devices shows `Claude Code Usage` with 14 entities.
- [ ] After 4 minutes of runtime, `sensor.claude_code_usage_burn_rate_pct_per_min` is non-null.
- [ ] `docker compose kill ccusage-mqtt` → HA marks all 14 sensors unavailable within 30s.
- [ ] `docker compose up -d` → sensors return online with retained values.

## Troubleshooting

- **All sensors stuck at `unknown`**: check `docker compose logs` for `AnthropicProbeError` or 401 — the API key is wrong or expired.
- **`mood` stuck at `idle` past 4 minutes**: your actual burn rate is below 0.10 %/min. This is normal — you're not using Claude heavily. Force a quick burst of usage and wait one more minute.
- **No `tokens_used` value**: `ccusage` couldn't find your JSONLs. Verify `${HOME}/.claude` exists on the host and that there's at least one session in `${HOME}/.claude/projects/`.
```

- [ ] **Step 6: Commit and tag the release**

```bash
git add README.md
git commit -m "docs(readme): acceptance checklist + troubleshooting"
git tag -a v0.1.0 -m "v0.1.0 — first working release"
```

---

## Self-review checklist (for the engineer to run after Task 17)

- [ ] All 14 sensors visible in HA → MQTT device page.
- [ ] `pytest tests/ -v` runs green from a fresh `.venv`.
- [ ] `.env` is mode `0600` and listed in `.gitignore` (the file is already there from the spec commit).
- [ ] `docker compose logs` is structured and parseable — no print-debug leftovers.
- [ ] Mood classification was hand-verified against `Clawdmeter/firmware/src/usage_rate.cpp` thresholds (this is what the golden test in Task 4 enforces).
- [ ] If `ccusage`'s JSON schema has drifted from the fixtures, parser tests + fixtures were updated together.

---

## Out of scope (deferred per spec §13)

- Lovelace card that consumes `sensor.claude_code_usage_mood` + clawd-sprite-extractor's `manifest.json` — separate spec.
- HA-side automations (low-quota alerts, daily spend summaries).
- Multi-host support.
- Historical persistence (HA Recorder already handles).
