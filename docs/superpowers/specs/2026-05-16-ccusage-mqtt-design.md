# ccusage-mqtt — Design

**Date:** 2026-05-16
**Owner:** George
**Status:** Approved, ready for implementation plan
**Repo:** `~/code/ccusage-mqtt/`

## 1. Goal

Publish Claude Code usage telemetry to MQTT with Home Assistant auto-discovery,
exposing a `Claude Code Usage` device with 14 sensors. The primary consumer is
a Home Assistant dashboard that mirrors the Clawdmeter ESP32 device's
behaviour — including a `mood` sensor that drives a Lovelace card showing the
matching pixel-art animation from `clawd-sprite-extractor`.

The `mood` sensor is the cardinal output. Everything else exists for
dashboards and alerts.

## 2. Background

The [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) ESP32 device
displays Claude Code 5h/7d utilization with a mood-driven splash animation. A
companion daemon polls Anthropic's API every 60s, parses the
`anthropic-ratelimit-unified-*` HTTP headers, and pushes them to the device
over BLE.

This project replicates the same telemetry surface as MQTT sensors so a Home
Assistant dashboard can render the same information (and animations) without
the ESP32.

**Sensor source-of-truth must match the firmware** so the firmware's
burn-rate thresholds (calibrated against Anthropic's 5h utilization header)
remain accurate. An earlier draft of this design computed
`session_pct` from local JSONL token counts via `ccusage`; that produces an
estimate, not the canonical Anthropic-issued utilization, and would have
mis-calibrated the mood thresholds. The final design uses the Anthropic
headers for canonical signals and `ccusage` for token/$ detail Anthropic
doesn't expose.

**Auth model:** the probe request uses the same Claude Code OAuth token the
Clawdmeter daemon reads from `~/.claude/.credentials.json`
(`claudeAiOauth.accessToken`). Using a developer/console API key would not
work: a console key has its own quota separate from Claude Pro/Max, so the
response headers would report dev-API usage, not Claude Code usage. The
publisher re-reads the credentials file on every poll so token refreshes
performed by Claude Code itself propagate automatically. Probe requests sent
with the OAuth token also require `anthropic-beta: oauth-2025-04-20` and a
`User-Agent: claude-code/<version>` header; both are mirrored from the
Clawdmeter daemon (`Clawdmeter/daemon/claude_usage_daemon.py:40-50`).

## 3. Architecture

Single Python container running on the OpenClaw host. Long-running
publisher with two pollers feeding a shared in-memory `State` and an MQTT
client.

```
        +-----------------------------+
host    |  ~/.claude/projects/        |  (bind-mount, read-only)
        +-----------------------------+
                       |
              +--------v---------+
              |  ccusage-mqtt    |   docker container
              |                  |
              |  +-----------+   |
              |  | headers   |---|--> POST /v1/messages (probe, 60s)
              |  | poller    |   |        Anthropic API
              |  +-----------+   |
              |  +-----------+   |
              |  | ccusage   |---|--> npx ccusage blocks --json (30s)
              |  | poller    |   |
              |  +-----------+   |
              |       |          |
              |  +----v------+   |
              |  | state +   |   |
              |  | burn rate |   |
              |  | + mood    |   |
              |  +----+------+   |
              |       |          |
              |  +----v------+   |
              |  | MQTT      |---|--> mosquitto on LAN
              |  | publisher |   |    (HA auto-discovery)
              |  +-----------+   |
              +------------------+
```

**Process model.** Single-threaded event loop. No asyncio, no threads. Both
pollers run on independent cadences in the same loop; state is updated and
sensors are published on every successful poll:

```
while not shutdown:
    if due(headers): poll_anthropic() -> state, ring.add(now, pct)
    if due(ccusage): poll_ccusage()   -> state
    state.burn_rate = ring.compute_rate()
    state.mood      = classify(state.burn_rate)
    publish_changed(state)
    sleep_until_next_due()
```

**Internal modules** (Python package `ccusage_mqtt`):

- `credentials.py` — reads `claudeAiOauth.accessToken` (and `expiresAt`) from
  Claude Code's `.credentials.json`; raises typed errors on missing /
  malformed files. Read per-poll so token rotation propagates.
- `anthropic_client.py` — issues the probe request (OAuth bearer +
  `anthropic-beta: oauth-2025-04-20` + `User-Agent: claude-code/<v>`),
  parses the `anthropic-ratelimit-unified-*` response headers (reset values
  are Unix epoch seconds in a string, not ISO 8601), returns a typed
  `RateLimitSnapshot`.
- `ccusage.py` — `subprocess.run(["npx", "ccusage", "blocks", "--json", "--offline"])`,
  parses the active block, returns a typed `BlockSnapshot`.
- `usage_rate.py` — ring-buffer + burn-rate computation + mood classifier.
  Direct port of `Clawdmeter/firmware/src/usage_rate.cpp` so calibration
  matches the firmware exactly.
- `state.py` — `State` dataclass holding all 14 sensor values + freshness
  timestamps; derivation functions for `time_to_limit_minutes`,
  `tokens_per_hour`, `spend_per_hour_usd`, `block_elapsed_pct`.
- `publisher.py` — `paho-mqtt` client, HA discovery payload builder, main
  loop, signal handling, structured logging.
- `__main__.py` — CLI entry point.

## 4. Sensor catalog

14 sensors, all under HA device:

- `identifiers`: `["claude_code_usage"]`
- `name`: `"Claude Code Usage"`
- `manufacturer`: `"openclaw"`
- `model`: `"ccusage-mqtt"`

| sensor_id | Unit | `device_class` | `state_class` | Icon (MDI) | Source | Computation |
|---|---|---|---|---|---|---|
| `session_pct` | `%` | — | `measurement` | `gauge` | API header | `5h-utilization × 100` |
| `session_reset_minutes` | `min` | — | `measurement` | `timer-sand` | API header | `(5h-reset − now) / 60`, clamp ≥0 |
| `session_status` | — | `enum` (`allowed`/`limited`/`unknown`) | — | `traffic-light` | API header | `5h-status` |
| `weekly_pct` | `%` | — | `measurement` | `gauge` | API header | `7d-utilization × 100` |
| `weekly_reset_minutes` | `min` | — | `measurement` | `timer-sand` | API header | `(7d-reset − now) / 60`, clamp ≥0 |
| `weekly_status` | — | `enum` (`allowed`/`limited`/`unknown`) | — | `traffic-light` | API header | `7d-status` |
| `burn_rate_pct_per_min` | `%/min` | — | `measurement` | `chart-line` | Derived | `Δsession_pct / Δmin` over 4-min ring; `null` until warm |
| `mood` | — | `enum` (`idle`/`normal`/`active`/`heavy`) | — | `emoticon` | Derived | Firmware thresholds on burn rate |
| `time_to_limit_minutes` | `min` | — | `measurement` | `timer-sand-complete` | Derived | `(100 − session_pct) / burn_rate`; `null` if rate ≤ 0 |
| `block_elapsed_pct` | `%` | — | `measurement` | `progress-clock` | Derived | `(300 − session_reset_minutes) / 300 × 100` |
| `tokens_used` | `tokens` | — | `total_increasing` | `format-letter-matches` | ccusage | `inputTokens + outputTokens + cacheCreationTokens + cacheReadTokens` |
| `tokens_per_hour` | `tokens/h` | — | `measurement` | `speedometer` | Derived | `tokens_used / block_elapsed_h`; `null` if elapsed < 1 min |
| `spend_so_far_usd` | `USD` | `monetary` | `total_increasing` | `currency-usd` | ccusage | block `costUSD` |
| `spend_per_hour_usd` | `USD/h` | — | `measurement` | `cash-clock` | Derived | `cost / block_elapsed_h`; `null` if elapsed < 1 min |

**Mood thresholds** — ported verbatim from `usage_rate.cpp:11-13`:

| Burn rate (%/min) | Mood |
|---|---|
| `< 0.10` | `idle` |
| `< 0.20` | `normal` |
| `< 0.33` | `active` |
| `≥ 0.33` | `heavy` |

## 5. Data flow

**Topology**

- HA discovery prefix: `homeassistant` (configurable)
- Base topic: `claude_code_usage` (configurable)
- Discovery topic per sensor: `homeassistant/sensor/claude_code_usage/<sensor_id>/config`
  (retained, published once at startup)
- State topic per sensor: `claude_code_usage/<sensor_id>/state`
  (retained, published on each cycle when value changes)
- Availability topic: `claude_code_usage/availability` (`online` / `offline`,
  retained; `offline` set via LWT)

**Payload shape.** State messages publish JSON `{"value": <num>}` or
`{"value": null}`. Discovery configs use `value_template: "{{ value_json.value }}"`;
`null` renders as `unknown` in HA — clean dashboards even during warm-up.

**Polling cadence**

- Header poller: 60s (matches Clawdmeter's daemon)
- ccusage poller: 30s
- Publish cycle: triggered after any poll; at most one publish per 30s

**Warm-up.** Ring buffer holds < 2 samples or spans < 240s →
`burn_rate_pct_per_min = null`, `mood = "idle"`. Mirrors firmware exactly
(`usage_rate.cpp:58-63`).

**Session reset detection.** When a new `session_pct` sample drops by ≥5
compared to the latest ring entry, flush the ring (port of
`usage_rate.cpp:47-49`). Cleanly handles the Anthropic 5h window roll-over.

**Probe request.** `POST /v1/messages` with:

- `Authorization: Bearer <claudeAiOauth.accessToken from ~/.claude/.credentials.json>`
- `anthropic-version: 2023-06-01`
- `anthropic-beta: oauth-2025-04-20`  (required for OAuth-authed requests)
- `User-Agent: claude-code/<version>`
- body: `{"model": "claude-haiku-4-5-20251001", "max_tokens": 1, "messages": [{"role":"user","content":"hi"}]}`

<1 input token per call. ~1440 calls/day. Charged against the user's Claude
Pro/Max 5h quota (the same one we're measuring) — under typical Claude Code
usage this is a sub-1% overhead on the budget. Verified against
`Clawdmeter/daemon/claude_usage_daemon.py:40-50`.

**Reset header format.** `anthropic-ratelimit-unified-{5h,7d}-reset` come back
as **Unix epoch seconds in a string** (e.g. `"1747396800.5"`), not ISO 8601.
The parser uses `float()` not `datetime.fromisoformat`.

## 6. Error handling

| Failure | Severity | Behavior |
|---|---|---|
| Anthropic API timeout/network error | recoverable | Skip cycle; retained values stand. After 3 consecutive failures, publish `session_status = "unknown"` and `weekly_status = "unknown"`. |
| Anthropic API 401/403 | fatal | Log endpoint + truncated response body, exit non-zero (code 2). Usually means the OAuth token expired and Claude Code hasn't run to refresh it. `docker compose logs` surfaces the issue. |
| `.credentials.json` missing / malformed | fatal | Log path + reason, exit non-zero (code 3). User needs to run Claude Code on the host to (re)create the file. |
| Anthropic API 429 | informational | Back off header poll to 5 min until next 200. The 429 *is* the signal: `session_status` is likely `"limited"`. |
| `ccusage` exits non-zero or emits invalid JSON | recoverable | Log warning. Token/$ sensors hold last values. If no active block was ever observed, those sensors stay `null`. |
| MQTT broker disconnect | automatic | `paho-mqtt`'s reconnect logic handles it. LWT fires `offline`. On reconnect, re-publish discovery configs + current state. |
| Container crash / OOM | external | LWT marks `availability = offline`; HA shows all sensors unavailable. `restart: unless-stopped` brings it back. |
| ccusage version drift | manual | Pin a known-good version in the Dockerfile. Bump deliberately during dependency updates. |

## 7. Configuration

`.env` file at repo root (mode `0600`, gitignored). `.env.example` is
checked in with placeholders.

| Var | Default | Purpose |
|---|---|---|
| `MQTT_HOST` | *required* | Broker hostname/IP |
| `MQTT_PORT` | `1883` | |
| `MQTT_USER` / `MQTT_PASS` | — | Optional broker auth |
| `MQTT_CLIENT_ID` | `ccusage-mqtt` | Stable client ID |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | HA auto-discovery prefix |
| `MQTT_BASE_TOPIC` | `claude_code_usage` | Root topic |
| `CLAUDE_CREDENTIALS_PATH` | `/data/claude-projects/.credentials.json` | Path inside container to Claude Code's OAuth credentials |
| `ANTHROPIC_API_BASE` | `https://api.anthropic.com` | Test override |
| `PROBE_MODEL` | `claude-haiku-4-5-20251001` | Cheapest model for probe |
| `CCUSAGE_PROJECTS_DIR` | `/data/claude-projects` | JSONL mount path inside container |
| `HEADER_POLL_SEC` | `60` | |
| `CCUSAGE_POLL_SEC` | `30` | |
| `BURN_RATE_WINDOW_SEC` | `240` | Ring-buffer span |
| `MOOD_IDLE_BELOW` | `0.10` | %/min threshold |
| `MOOD_NORMAL_BELOW` | `0.20` | %/min threshold |
| `MOOD_ACTIVE_BELOW` | `0.33` | %/min threshold |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

## 8. Repo layout

```
ccusage-mqtt/
├── README.md
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── .gitignore
├── pyproject.toml
├── ccusage_mqtt/
│   ├── __init__.py
│   ├── __main__.py
│   ├── credentials.py
│   ├── anthropic_client.py
│   ├── ccusage.py
│   ├── usage_rate.py
│   ├── state.py
│   └── publisher.py
├── tests/
│   ├── test_usage_rate.py
│   ├── test_state.py
│   ├── test_credentials.py
│   ├── test_anthropic_parse.py
│   └── test_ccusage_parse.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-05-16-ccusage-mqtt-design.md  (this file)
```

## 9. Container

**Dockerfile**

- Base: `node:22-alpine`
- Install Python 3, pip, paho-mqtt, requests via apk + pip
- `npm i -g ccusage@<pinned>` (pin a specific version)
- Copy package, `ENTRYPOINT ["python3", "-m", "ccusage_mqtt"]`
- Image: ~200 MB

**docker-compose.yml**

```yaml
services:
  ccusage-mqtt:
    build: .
    container_name: ccusage-mqtt
    restart: unless-stopped
    env_file: .env
    volumes:
      - ${HOME}/.claude/projects:/data/claude-projects:ro
    network_mode: host
```

`network_mode: host` so the container can reach the broker on the LAN
without docker-networking gymnastics.

## 10. Deployment

```bash
cd ~/code/ccusage-mqtt
cp .env.example .env && $EDITOR .env
docker compose up -d --build
docker compose logs -f
```

HA discovers the 14 sensors within ~60s of container start.

## 11. Testing

- Unit: `usage_rate.py` ring math, mood thresholds, reset detection — pure
  functions, no I/O. **Golden test:** run the same sample sequence through
  this code as through `Clawdmeter/firmware/src/usage_rate.cpp` (compiled
  standalone with a small harness); the group classifications must match
  frame-for-frame. This is the calibration guarantee.
- Unit: `state.py` sensor derivations given mock header + ccusage inputs.
- Unit: parsers for `anthropic_client.py` and `ccusage.py` against captured
  real-world fixture files.
- Integration (optional CI): mosquitto in a sidecar container, mocked
  Anthropic + mocked ccusage subprocess, assert all 14 discovery configs +
  state messages are received with correct shapes.
- No live Anthropic calls in CI.

## 12. Acceptance criteria

1. `docker compose up -d --build` brings the container up cleanly with a
   valid `.env`.
2. Within 60s, HA auto-discovers a `Claude Code Usage` device with all 14
   sensors.
3. During warm-up (< 4 min), `mood = "idle"` and `burn_rate_pct_per_min = null`.
   After ~4 min of running, `burn_rate_pct_per_min` is non-null and `mood`
   classifies according to the firmware thresholds for the actual burn rate
   (i.e. it may transition away from `idle`).
4. `docker compose kill` marks all sensors `unavailable` in HA within 30s;
   `docker compose up -d` restores them with current retained values.
5. A simulated session reset (utilization drops ≥5%) flushes the ring
   buffer; `mood` returns to `idle` until 4 min of fresh samples accumulate.
6. Anthropic 401 → container exits with code 2; `docker logs` shows the
   failing endpoint and a truncated response body (most likely cause is an
   expired OAuth token — running Claude Code on the host refreshes it).
   Missing/malformed `.credentials.json` → exit code 3 with a clear message.
7. `ccusage` subprocess failure → token/$ sensors hold their last values;
   API-header sensors continue to update.
8. Unit tests for `usage_rate.py` produce the same group classification as
   `Clawdmeter/firmware/src/usage_rate.cpp` for an identical sample
   sequence (golden test against the C source).
9. `.gitignore` blocks `.env`, `__pycache__/`, `.venv/`, and any captured
   fixture files containing real API responses.

## 13. Out of scope (future specs)

- The Lovelace card that watches `sensor.claude_code_usage_mood` and renders
  the matching WebP from `clawd-sprite-extractor`'s `manifest.json`.
- HA-side automations (low-quota alerts, daily spend summaries) — trivial
  once the sensors exist.
- Multi-host support — one container per Claude Code installation.
- Historical metrics persistence — HA Recorder + statistics already cover
  this.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Anthropic deprecates the `unified-*` ratelimit headers | Clawdmeter would break too; we'd track its fix and follow. |
| `ccusage` schema or CLI flags change | Pin a version in the Dockerfile. Bump deliberately. |
| Probe request inflates own utilization | Haiku at `max_tokens=1` is sub-1% of the 5h budget per day. Acceptable. |
| Anthropic 5h window roll-over de-syncs with our header poll | Header is canonical; ring-buffer flush on ≥5% drop handles roll-over within one poll cycle. |
| API key in `.env` leaked | File mode `0600`, gitignored, not in image. Key is scoped to a single host. |

## 15. Open questions to revisit

- Does `POST /v1/messages/count_tokens` return the same ratelimit headers
  without billing? Worth a 10-minute test once the project ships — switching
  is a one-function change in `anthropic_client.py`.
- Should the `mood` enum gain a `cold` state for the warm-up window, or is
  `idle` (current plan) good enough? Sticking with `idle` for now — matches
  firmware behavior, simplest UX.
