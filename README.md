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

- A working Claude Code installation on the host (the publisher reads
  `~/.claude/.credentials.json` for the OAuth token — no separate
  Anthropic API key is required)
- Node.js + npm if installing on-host (the publisher shells out to
  [`ccusage`](https://github.com/ryoppippi/ccusage) for token/$ totals).
  Not needed if you use the Docker path — the image installs it.
- Python ≥ 3.12 if installing on-host
- A Home Assistant instance with an MQTT broker reachable on your LAN.
  See **Home Assistant MQTT setup** below if you don't have one yet.

## Install

### Option A: Docker (recommended)

Self-contained. Bundles Node + `ccusage` + Python in one image.

```bash
git clone https://github.com/george-vice/ccusage-mqtt.git
cd ccusage-mqtt
./setup.sh                  # prompts for MQTT broker + per-instance identity
docker compose up -d --build
docker compose logs -f
```

### Option B: From source (no Docker)

Runs as a regular Python process. Use this if you don't want Docker, or
want to manage it under `systemd`/`launchd`/etc.

```bash
# 1. Install ccusage (Node) globally — the publisher shells out to it
npm install -g ccusage

# 2. Install the publisher
git clone https://github.com/george-vice/ccusage-mqtt.git
cd ccusage-mqtt
pip install .
# (or `pipx install .` for isolation)

# 3. Configure
./setup.sh                  # writes ./.env in the repo root
# (or skip setup.sh and set env vars by hand — MQTT_HOST is the only required one)

# 4. Run
ccusage-mqtt                # picks up ./.env from the current directory
# or:
ccusage-mqtt --env-file /path/to/anywhere/.env
```

In source mode, defaults resolve to `~/.claude/.credentials.json` and
`~/.claude/projects/` on disk automatically — no docker bind-mount to
worry about. To run it as a service, drop something like this into
`~/.config/systemd/user/ccusage-mqtt.service`:

```ini
[Unit]
Description=ccusage-mqtt — Claude Code → MQTT publisher
After=network-online.target

[Service]
ExecStart=%h/.local/bin/ccusage-mqtt --env-file %h/.config/ccusage-mqtt/.env
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

Then `systemctl --user enable --now ccusage-mqtt`.

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

After either install path, Home Assistant auto-discovers a `Claude Code Usage`
device with 15 entities within ~60s under Settings → Devices & Services → MQTT.

Re-run `./setup.sh` any time to reconfigure, or hand-edit `.env` directly.

## Running multiple Claude accounts

To track two Claude accounts (e.g. work + personal) from the same host, create
a separate Claude Code config directory for each account, then run one
`ccusage-mqtt` container per directory. Each appears as its own device in
Home Assistant.

```bash
# 1. Log in to each account into its own config dir on the host
CLAUDE_CONFIG_DIR=~/.claude-work     claude login
CLAUDE_CONFIG_DIR=~/.claude-personal claude login

# 2. Clone the repo once per instance
git clone https://github.com/george-vice/ccusage-mqtt.git ~/code/ccusage-mqtt-work
git clone https://github.com/george-vice/ccusage-mqtt.git ~/code/ccusage-mqtt-personal

# 3. Run setup.sh in each, answering with distinct values
cd ~/code/ccusage-mqtt-work
./setup.sh
#   HA device name:               Claude Code (Work)
#   Account label:                work
#   Claude Code config dir:       /home/you/.claude-work
docker compose up -d --build

cd ~/code/ccusage-mqtt-personal
./setup.sh
#   HA device name:               Claude Code (Personal)
#   Account label:                personal
#   Claude Code config dir:       /home/you/.claude-personal
docker compose up -d --build
```

When `ACCOUNT_NAME` is set, `setup.sh` automatically suffixes
`MQTT_BASE_TOPIC` and `MQTT_CLIENT_ID` with a slug of the account name so
the two instances can't collide on the broker. The HA device names you give
keep them visually distinct, and every MQTT state payload carries
`"account": "<name>"` so external subscribers can tell them apart too.

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
firmware. All 15 sensors publish to MQTT with the `retain` flag so values
survive broker restarts.

When an access token expires, the publisher uses the stored `refreshToken`
to mint a fresh one at Anthropic's OAuth endpoint and writes it back to the
credentials file in-place — no manual `claude login` needed. If the refresh
itself fails (only happens if the refresh token has also expired), the
container does **not** crash-loop: it marks `session_status: "unknown"` in
HA and keeps publishing token/$ sensors from `ccusage`, self-healing on the
next poll once you re-login.

Both Claude **Pro/Max** and **Enterprise** plans are supported. Enterprise
returns a different ratelimit header schema (no 7-day window — overage
allowance instead); the parser handles both. Enterprise users get the
`session_*` sensors populated from overage data; `weekly_*` sensors stay
`null` (correct, not a bug).

## Troubleshooting

- **All sensors stuck at `unknown`** — the OAuth probe is failing. Check
  `docker compose logs <container>` for the exact error. The publisher
  tries to auto-refresh the access token using the stored `refreshToken`;
  if you see `OAuth refresh failed: 400 invalid_grant`, the refresh
  token has also expired and a manual re-login is required:
  `rm ~/.claude-work/.credentials.json && CLAUDE_CONFIG_DIR=~/.claude-work claude`
  (substitute your actual config dir). The publisher self-heals within
  the next 60s poll cycle.
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

MIT — see [LICENSE](LICENSE).
