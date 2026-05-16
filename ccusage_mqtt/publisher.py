from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryConfig:
    sensor_id: str
    topic: str
    payload: str  # JSON-serialized


# (sensor_id, friendly_name, unit, device_class, state_class, icon, display_precision, [enum options or None])
# display_precision = `suggested_display_precision` (HA UI rounding hint; ignored
# for enum sensors). None = HA uses default (full precision).
_SENSOR_SPECS: tuple[tuple[str, str, str | None, str | None, str | None, str, int | None, list[str] | None], ...] = (
    ("session_pct",            "Session %",         "%",        None,        "measurement",      "mdi:gauge",                 1,    None),
    ("session_reset_minutes",  "Session resets in", "min",      None,        "measurement",      "mdi:timer-sand",            0,    None),
    ("session_status",         "Session status",    None,       "enum",      None,               "mdi:traffic-light",         None, ["allowed", "limited", "unknown"]),
    ("weekly_pct",             "Weekly %",          "%",        None,        "measurement",      "mdi:gauge",                 1,    None),
    ("weekly_reset_minutes",   "Weekly resets in",  "min",      None,        "measurement",      "mdi:timer-sand",            0,    None),
    ("weekly_status",          "Weekly status",     None,       "enum",      None,               "mdi:traffic-light",         None, ["allowed", "limited", "unknown"]),
    ("burn_rate_pct_per_min",  "Burn rate",         "%/min",    None,        "measurement",      "mdi:chart-line",            3,    None),
    ("mood",                   "Mood",              None,       "enum",      None,               "mdi:emoticon",              None, ["idle", "normal", "active", "heavy"]),
    ("time_to_limit_minutes",  "Time to limit",     "min",      None,        "measurement",      "mdi:timer-sand-complete",   0,    None),
    ("block_elapsed_pct",      "Block elapsed",     "%",        None,        "measurement",      "mdi:progress-clock",        1,    None),
    ("tokens_used",            "Tokens used",       "tokens",   None,        "total_increasing", "mdi:format-letter-matches", 0,    None),
    ("tokens_per_hour",        "Tokens per hour",   "tokens/h", None,        "measurement",      "mdi:speedometer",           0,    None),
    ("spend_so_far_usd",       "Spend so far",      "USD",      "monetary",  "total_increasing", "mdi:currency-usd",          2,    None),
    ("spend_per_hour_usd",     "Spend per hour",    "USD/h",    None,        "measurement",      "mdi:cash-clock",            2,    None),
)


def build_discovery_configs(
    *,
    device_id: str,
    device_name: str,
    base_topic: str,
    discovery_prefix: str = "homeassistant",
    include_account_attribute: bool = False,
) -> list[DiscoveryConfig]:
    device_block = {
        "identifiers": [device_id],
        "name": device_name,
        "manufacturer": "ccusage-mqtt",
        "model": "Claude Code usage publisher",
    }
    configs: list[DiscoveryConfig] = []
    for sid, fname, unit, dclass, sclass, icon, precision, options in _SENSOR_SPECS:
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
        if precision is not None:
            body["suggested_display_precision"] = precision
        if options is not None:
            body["options"] = options
        if include_account_attribute:
            # Surface the per-payload `account` field as an HA attribute on
            # every sensor entity. Automations can then filter on
            # `state_attr('sensor.claude_code_usage_mood', 'account')`.
            body["json_attributes_topic"] = f"{base_topic}/{sid}/state"
            body["json_attributes_template"] = "{{ {'account': value_json.account} | tojson }}"
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
    account_name: str | None = None


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
            payloads=self._state.to_mqtt_payloads(account=self._cfg.account_name),
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
