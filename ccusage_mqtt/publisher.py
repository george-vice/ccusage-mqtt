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
