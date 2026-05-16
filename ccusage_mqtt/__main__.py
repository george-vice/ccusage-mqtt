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
