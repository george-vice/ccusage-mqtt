from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ccusage_mqtt.anthropic_client import AnthropicAuthError, probe
from ccusage_mqtt.ccusage import run as ccusage_run
from ccusage_mqtt.credentials import (
    CredentialsMalformed,
    CredentialsMissing,
    load_claude_credentials,
)
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
    mqtt_device_name: str
    account_name: str | None

    claude_credentials_path: str
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


def _default_claude_dir() -> str:
    """The default Claude Code config dir for the current process.

    Inside the docker image, docker-compose mounts the host's config at
    /data/claude-projects and sets CCUSAGE_PROJECTS_DIR explicitly. For
    terminal installs (pip / pipx / from-source), fall back to the user's
    real ~/.claude on disk.
    """
    if Path("/data/claude-projects").is_dir():
        return "/data/claude-projects"
    return os.path.expanduser("~/.claude")


def load_config_from_env(env: Mapping[str, str]) -> AppConfig:
    claude_dir = env.get("CCUSAGE_PROJECTS_DIR") or _default_claude_dir()
    return AppConfig(
        mqtt_host=_required(env, "MQTT_HOST"),
        mqtt_port=int(env.get("MQTT_PORT", "1883")),
        mqtt_user=env.get("MQTT_USER") or None,
        mqtt_pass=env.get("MQTT_PASS") or None,
        mqtt_client_id=env.get("MQTT_CLIENT_ID", "ccusage-mqtt"),
        mqtt_discovery_prefix=env.get("MQTT_DISCOVERY_PREFIX", "homeassistant"),
        mqtt_base_topic=env.get("MQTT_BASE_TOPIC", "claude_code_usage"),
        mqtt_device_name=env.get("MQTT_DEVICE_NAME", "Claude Code Usage"),
        account_name=env.get("ACCOUNT_NAME") or None,
        claude_credentials_path=env.get(
            "CLAUDE_CREDENTIALS_PATH",
            os.path.join(claude_dir, ".credentials.json"),
        ),
        anthropic_api_base=env.get("ANTHROPIC_API_BASE", "https://api.anthropic.com"),
        probe_model=env.get("PROBE_MODEL", "claude-haiku-4-5-20251001"),
        ccusage_projects_dir=claude_dir,
        header_poll_sec=float(env.get("HEADER_POLL_SEC", "60")),
        ccusage_poll_sec=float(env.get("CCUSAGE_POLL_SEC", "30")),
        burn_rate_window_sec=float(env.get("BURN_RATE_WINDOW_SEC", "240")),
        mood_idle_below=float(env.get("MOOD_IDLE_BELOW", "0.10")),
        mood_normal_below=float(env.get("MOOD_NORMAL_BELOW", "0.20")),
        mood_active_below=float(env.get("MOOD_ACTIVE_BELOW", "0.33")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )


def load_env_file(path: str | os.PathLike) -> int:
    """Read KEY=VALUE lines from path into os.environ.

    Tiny dotenv-style parser — handles comments and blank lines, strips
    quotes around values. Doesn't overwrite existing env vars (so explicit
    `MQTT_HOST=foo python -m ccusage_mqtt` beats whatever is in .env).
    Returns the count of vars set. No-ops if path doesn't exist.
    """
    p = Path(path)
    if not p.is_file():
        return 0
    count = 0
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            count += 1
    return count


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def _parse_argv(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ccusage-mqtt",
        description="Publish Claude Code usage telemetry to MQTT for Home Assistant.",
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Load environment variables from this file before reading config "
             "(default: ./.env in the current working directory; silently skipped "
             "if it doesn't exist). Existing environment variables take precedence.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(argv)
    n_loaded = load_env_file(args.env_file)
    cfg = load_config_from_env(os.environ)
    _setup_logging(cfg.log_level)
    log = logging.getLogger("ccusage_mqtt")
    if n_loaded:
        log.info("loaded %d vars from %s", n_loaded, args.env_file)
    log.info("starting ccusage-mqtt (host=%s port=%s)", cfg.mqtt_host, cfg.mqtt_port)

    discovery = build_discovery_configs(
        device_id=cfg.mqtt_base_topic,
        device_name=cfg.mqtt_device_name,
        base_topic=cfg.mqtt_base_topic,
        discovery_prefix=cfg.mqtt_discovery_prefix,
        include_account_attribute=bool(cfg.account_name),
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
    mqtt_client.publish_discovery(discovery)

    def poll_headers():
        # Re-read each poll so Claude Code's background token refresh propagates.
        creds = load_claude_credentials(cfg.claude_credentials_path)
        return probe(
            access_token=creds.access_token,
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
            account_name=cfg.account_name,
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
            except CredentialsMissing as e:
                log.error("claude credentials missing — exiting: %s", e)
                return 3
            except CredentialsMalformed as e:
                log.error("claude credentials malformed — exiting: %s", e)
                return 3
            time.sleep(min(5.0, cfg.ccusage_poll_sec / 2.0))
    finally:
        mqtt_client.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
