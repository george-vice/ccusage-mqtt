# CLAUDE.md — ccusage-mqtt project orientation

This file is the first thing any Claude-family agent (Code, Cowork, etc.)
should read when picking up this repo. It gives a self-contained snapshot of
what the project does, how it's built, what's live, and what's still open.

For deep detail, the full design spec is in [`docs/design.md`](docs/design.md).

## What this is

A Python container that publishes Claude Code usage telemetry to MQTT with
Home Assistant auto-discovery. Mirrors the
[Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) ESP32 firmware's
telemetry surface so the same `mood` thresholds apply across both.

On any host with Claude Code installed, it stands up a `Claude Code Usage`
device in HA with 15 sensors: 5h/7d window utilization %, reset countdowns,
status enums, current burn rate %/min, a `mood` enum (idle/normal/active/heavy)
classified from the burn rate using the firmware's exact thresholds, tokens
used + spend so far + hourly rates, plus a dedicated `Account` label sensor.

Auth uses the OAuth token Claude Code already stores in
`~/.claude/.credentials.json` — no separate Anthropic API key required.

## Current state (as of 2026-05-17)

**Two instances are live on the OpenClaw host, both verified working
end-to-end in HA:**

| Container | Reads from | Anthropic plan | HA device label | MQTT base topic |
|---|---|---|---|---|
| `ccusage-mqtt-personal` | `~/.claude/` | Max | `Claude Code (Personal)` | `claude_code_usage_personal` |
| `ccusage-mqtt-work` | `~/.claude-work/` | Enterprise | `Claude Code (Work)` | `claude_code_usage_work` |

Each container reads a different host directory (different Claude account),
publishes to a non-colliding MQTT topic, runs under a unique container name,
and appears as its own device in Home Assistant.

The repo at the OpenClaw host's `~/code/ccusage-mqtt/` is the personal
instance; `~/code/ccusage-mqtt-work/` is the work instance. Both check out
the same GitHub repo: https://github.com/george-vice/ccusage-mqtt

**PR #1 is merged.** Everything since (Docker container-name fix, Enterprise
header support, etc.) has been small hot-fixes direct to `master`.

## Architecture

Single-threaded Python event loop with two pollers feeding a shared `State`,
which is serialized to retained MQTT payloads:

```
~/.claude{,-work}/         (host, bind-mounted ro into the container)
        |
        v
 +-----------------+
 | anthropic_client|---> POST /v1/messages (probe, 60s)
 |   credentials   |    OAuth bearer + anthropic-beta: oauth-2025-04-20
 +--------+--------+
          |  RateLimitSnapshot
          v
 +-----------------+
 | ccusage        |---> npx ccusage blocks --json --offline (30s)
 +--------+--------+
          |  BlockSnapshot
          v
 +-----------------+
 | usage_rate      |---> RingBuffer + compute_rate + classify_mood
 +--------+--------+         (port of Clawdmeter usage_rate.cpp)
          |
          v
 +-----------------+
 | state           |---> apply_*, recompute_derived, to_mqtt_payloads
 +--------+--------+
          |
          v
 +-----------------+
 | publisher      |---> paho-mqtt v2: LWT, retained, re-discovery on connect
 |   PublisherLoop|
 +-----------------+
```

## File / module map

| File | Responsibility |
|---|---|
| `ccusage_mqtt/credentials.py` | Read `claudeAiOauth.accessToken` from `~/.claude/.credentials.json` (re-read per poll so token rotation propagates). Also implements `refresh_claude_credentials()` which uses the stored `refreshToken` to mint a fresh access token at Anthropic's OAuth endpoint when the current one expires |
| `ccusage_mqtt/anthropic_client.py` | OAuth-authed probe to `/v1/messages`; parses both Pro/Max (`5h-*` / `7d-*`) **and** Enterprise (`overage-*` / `unified-*`) ratelimit headers via fallback chains |
| `ccusage_mqtt/ccusage.py` | Subprocess wrapper for the `ccusage` npm CLI; parses active-block JSON; emits `CcusageError` on subprocess failure |
| `ccusage_mqtt/usage_rate.py` | RingBuffer + `compute_rate` (%/min with 4-min warm-up guard) + `detect_reset` (≥5% drop → flush) + `classify_mood` Literal — direct port of `Clawdmeter/firmware/src/usage_rate.cpp` so calibration matches |
| `ccusage_mqtt/state.py` | `State` dataclass with apply methods + `recompute_derived` (burn_rate → mood, time_to_limit, %/h, $/h) + `to_mqtt_payloads(account=…)` |
| `ccusage_mqtt/publisher.py` | `DiscoveryConfig` + `build_discovery_configs` (15 HA sensors), `MqttClient` (paho v2 with LWT + retained + dedup + re-discovery on reconnect), `PublisherLoop` (two-poller scheduling, 429 backoff, stale-header detection) |
| `ccusage_mqtt/__main__.py` | env config loader + tiny dotenv parser + `--env-file` CLI flag + signal-safe main loop |
| `Dockerfile` | `node:22-alpine` + Python 3 + `ccusage@18.0.11` (pinned) + non-root user (uid 10001) |
| `docker-compose.yml` | Compose with `${CONTAINER_NAME:-ccusage-mqtt}`, `user: "${USER_UID:-1000}:${USER_GID:-1000}"`, bind-mount `${CLAUDE_CONFIG_HOST_PATH:-${HOME}/.claude}` |
| `setup.sh` | Interactive `.env` generator. Prompts for broker creds + per-instance identity; auto-slugs `MQTT_BASE_TOPIC` / `MQTT_CLIENT_ID` / `CONTAINER_NAME` from `ACCOUNT_NAME` |
| `tests/` | 98 passing tests, pytest. Pure-function unit tests for `usage_rate`, parsers; mocked `responses` for HTTP probe; mocked paho for MQTT client |

## Key design decisions (with rationale — *do not relitigate*)

1. **OAuth not API key.** The probe uses Claude Code's OAuth token, not
   `ANTHROPIC_API_KEY`. Reason: a developer console key has a separate quota
   from Pro/Max/Enterprise — its ratelimit headers wouldn't reflect actual
   Claude Code usage. Header `anthropic-beta: oauth-2025-04-20` is required.
   See `anthropic_client.probe()`. Mirrors what Clawdmeter's daemon does.

2. **Reset timestamps are Unix epoch seconds in a string**, NOT ISO 8601.
   The parser uses `float()` not `datetime.fromisoformat`. Earlier draft had
   ISO parsing; only the live deployment surfaced the bug.

3. **Enterprise plans return different headers.** Pro/Max returns
   `5h-utilization` / `7d-utilization` / etc. Enterprise returns
   `overage-utilization` / `unified-status` / `unified-reset` — no 5h or 7d
   window concept. `parse_ratelimit_headers` tries both schemas via a
   fallback chain per field. Weekly sensors stay null on Enterprise — that's
   accurate, not a bug.

4. **`mood` classifier ports the firmware verbatim** (strict `<` on each
   threshold so values equal to a boundary fall into the upper bucket).
   `classify_mood(0.10, idle_below=0.10, ...)` returns `"normal"`, not
   `"idle"`. There's a golden test against the firmware behavior.

5. **`account` always appears in every payload** (defaults to `"default"`
   when `ACCOUNT_NAME` is unset). Plus a dedicated `Account` sensor surfaces
   the label in the HA device card. This was added after a live screenshot
   showed there was no obvious place for the account label.

6. **Container runs as host UID/GID via docker-compose override.** The
   credentials file is `chmod 600` owned by the host user; without UID
   alignment the bind-mount is unreadable in the container. `setup.sh`
   writes `USER_UID` / `USER_GID`; compose uses
   `user: "${USER_UID:-1000}:${USER_GID:-1000}"`.

7. **Container name is parameterized** (`${CONTAINER_NAME:-ccusage-mqtt}`)
   so two instances on the same host can coexist. Setup.sh auto-suffixes
   from the account slug.

8. **Polling cadences and burn-rate window match firmware:** 60s header
   poll, 30s ccusage poll, 240s ring buffer window (4 min warm-up before
   `burn_rate_pct_per_min` becomes non-null).

9. **OAuth tokens self-refresh.** When the probe returns 401, `__main__.poll_headers` calls `refresh_claude_credentials()`, which POSTs to `https://platform.claude.com/v1/oauth/token` with the stored `refreshToken` and Claude Code's hardcoded client_id (`9d1c250a-e61b-44d9-88ed-5944d1962f5e`, reverse-engineered from the CLI binary). Writes rotated tokens back to the credentials file. If the refresh fails, the container does NOT crash-loop anymore — headers go stale (`status: "unknown"`) and we keep publishing token/$ sensors from ccusage. User fix: `CLAUDE_CONFIG_DIR=… claude` once.

10. **Value precision is rounded at the publisher** to kill IEEE-754 noise
   (`0.07 × 100` → `7.000000000000001`) plus each sensor's HA discovery
   config carries `suggested_display_precision`. Don't let raw floats leak
   into MQTT.

## What's done

- ✅ Full implementation against the spec
- ✅ MIT license + clean OSS-ready repo (no internal references)
- ✅ Terminal / `pip install` path (not just Docker) — `[project.scripts]` entry
  + `--env-file` flag + `~/.claude/` defaults when not in container
- ✅ Multi-account support (Pro/Max + Enterprise headers)
- ✅ Interactive `setup.sh` for the per-instance vars
- ✅ Two-instance deployment verified live in HA
- ✅ All 98 tests pass

## What's open / candidates for follow-up

- **Lovelace dashboard YAML** — written but not yet pasted into the user's
  HA instance. Find it in the conversation history; copy into
  Settings → Dashboards → ⋮ → Raw configuration editor. Two side-by-side
  device cards, one per instance.
- **GitHub Actions** — no CI yet. `pytest` is the only check.
- **PyPI release** — `pyproject.toml` is publish-ready; never been built.
  `pip install ccusage-mqtt` doesn't work yet.
- **systemd-user unit template** is in README but no install helper.
- **Better Enterprise sensors** — the Enterprise schema also exposes
  `unified-overage-in-use`, `unified-representative-claim`,
  `unified-fallback-percentage`. None of these are surfaced as MQTT sensors
  today. Worth considering for Enterprise users.
- **Version bump.** `pyproject.toml` still says `0.1.0`. With everything
  shipped since the initial PR (multi-instance, terminal install,
  Enterprise support, account-always-on), `0.2.0` is justified.

## How to work in this repo

```bash
# Test
python3 -m pytest tests/ -q                     # 98 tests, runs in <1s

# Re-deploy a running instance after code changes
cd ~/code/ccusage-mqtt          # or ccusage-mqtt-work
docker compose up -d --build

# Inspect what's flowing on MQTT
docker exec ccusage-mqtt-personal python3 -c "..."   # see conversation
```

User preference: every change goes through a branch + PR on GitHub. Small
hot-fixes have gone direct to `master`, which is fine for follow-up tweaks;
larger features should be PRs.

## Repo URLs

- GitHub: https://github.com/george-vice/ccusage-mqtt
- License: MIT
- Default branch: `master`
- Active branches: `master` only (the original `impl/initial` was merged via PR #1)
