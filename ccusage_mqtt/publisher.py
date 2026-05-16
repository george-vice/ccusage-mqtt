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
        "manufacturer": "openclaw",
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
