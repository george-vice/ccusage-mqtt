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
