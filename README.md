# ccusage-mqtt

Publishes Claude Code usage telemetry to MQTT with Home Assistant
auto-discovery. Mirrors the
[Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) ESP32 firmware's
telemetry surface so the same `mood` / burn-rate thresholds apply.

Once running, your Home Assistant gains a `Claude Code Usage` device with 14
sensors — current 5h and 7d utilisation %, burn rate %/min, an enum `mood`
sensor (idle / normal / active / heavy), time-to-limit, tokens & spend so
far, hourly token and spend rates, and reset countdowns for both windows.

See `docs/superpowers/specs/2026-05-16-ccusage-mqtt-design.md` for the full
design.

## Prerequisites

You'll need a host with:

- Docker + Docker Compose
- An active Claude Code installation on the same host (the publisher reads
  `~/.claude/.credentials.json` for the OAuth token — no separate Anthropic
  API key is required)
- A Home Assistant instance with MQTT — see below

## Home Assistant MQTT setup

`ccusage-mqtt` is a publisher. Home Assistant needs an MQTT broker to
receive the messages and the MQTT integration to render them. If you don't
already have those, set them up first:

1. **Install an MQTT broker.** The simplest path on Home Assistant OS is the
   **Mosquitto broker** add-on:
   - Settings → Add-ons → Add-on Store → "Mosquitto broker" → Install → Start.
   - Any other MQTT broker on your LAN (a standalone mosquitto, EMQX, HiveMQ,
     etc.) works equally well — skip this step and point at your existing
     broker in step 3.

2. **Create a Home Assistant user for MQTT** (recommended — keeps broker
   credentials separate from your login).
   - Settings → People → Users → Add User. Give it a username and password.
     "Can only log in from the local network" is fine.

3. **Add the MQTT integration to HA.**
   - Settings → Devices & Services → Add Integration → "MQTT".
   - Broker: the hostname/IP of your broker. If you're using the Mosquitto
     add-on, this is your HA host (e.g. `homeassistant.local`, or your
     server's LAN IP). With the add-on, `core-mosquitto` also works from
     other add-ons but not from external hosts.
   - Username / password: the user from step 2.
   - **MQTT auto-discovery should be enabled by default.** This is what makes
     the 14 sensors appear automatically when `ccusage-mqtt` starts publishing.

That's the HA side. You'll re-enter the broker hostname and the same
username/password into `./setup.sh` below.

## Quick start

```bash
./setup.sh                  # prompts for MQTT broker host / port / user / pass
docker compose up -d --build
docker compose logs -f
```

Within ~60s, Home Assistant should auto-discover a `Claude Code Usage`
device with 14 entities under Settings → Devices & Services → MQTT.

Re-run `./setup.sh` any time to reconfigure. Hand-editing `.env` works too:
`cp .env.example .env && $EDITOR .env`.

## Configuration

`./setup.sh` only asks for the four values you almost always need to set
(MQTT host / port / user / pass). Everything else takes sensible defaults
from `.env.example`. Notable optional knobs:

| Var | Default | Purpose |
|---|---|---|
| `CLAUDE_CREDENTIALS_PATH` | `/data/claude-projects/.credentials.json` | Where the container finds Claude Code's OAuth token. The default works with the `docker-compose.yml` bind-mount; only change if you mount `~/.claude` somewhere else. |
| `PROBE_MODEL` | `claude-haiku-4-5-20251001` | Model used for the ratelimit-probe API call. Cheapest current Anthropic model. |
| `HEADER_POLL_SEC` | `60` | How often to refresh the 5h / 7d utilisation from Anthropic |
| `CCUSAGE_POLL_SEC` | `30` | How often to refresh token / cost data from `ccusage` |
| `BURN_RATE_WINDOW_SEC` | `240` | Ring-buffer span for burn-rate smoothing. 240 matches the Clawdmeter firmware. |
| `MOOD_*_BELOW` | `0.10 / 0.20 / 0.33` | Mood threshold %/min boundaries. Defaults match the Clawdmeter firmware. |

## How it works

Two pollers run in a single Python process:

- **Anthropic API headers** (every 60s). Sends a 1-token probe to
  `/v1/messages` using your Claude Code OAuth token and reads the
  `anthropic-ratelimit-unified-*` response headers — the authoritative
  source for current 5h / 7d window % used, reset times, and allowed/limited
  status.
- **`ccusage` CLI** (every 30s). Reads your local Claude Code session JSONLs
  to get current-block token count and cost in USD.

A ring buffer over the 5h % samples produces a smoothed burn-rate (%/min),
which classifies into one of four moods using the same thresholds as the
firmware. All 14 sensors publish to MQTT with the `retain` flag so values
survive broker restarts.

## Troubleshooting

- **All sensors stuck at `unknown`** — the OAuth probe is failing. Check
  `docker compose logs ccusage-mqtt`. Most commonly: the token expired and
  Claude Code hasn't run on the host since. Run `claude` (or any Claude Code
  command) once to refresh.
- **`mood` stuck at `idle` past 4 minutes** — your actual burn rate is below
  0.10 %/min. This is normal — Claude Code isn't being used heavily right
  now. Lower `MOOD_IDLE_BELOW` if you want a more sensitive scale.
- **No `tokens_used` value** — `ccusage` couldn't find your session JSONLs.
  Verify `~/.claude/projects/` exists on the host with at least one session
  in it.
- **HA never sees the device** — check that auto-discovery is enabled on the
  MQTT integration (Settings → Devices & Services → MQTT → Configure → "Enable
  discovery"). With it enabled, the device should appear within 60s of the
  container starting.

## License

TBD before going public.
