import json

from ccusage_mqtt.publisher import DiscoveryConfig, build_discovery_configs


def test_returns_14_configs():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    assert len(cfgs) == 14


def test_each_config_has_required_ha_fields():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    for cfg in cfgs:
        assert isinstance(cfg, DiscoveryConfig)
        body = json.loads(cfg.payload)
        assert body["unique_id"].startswith("claude_code_usage_")
        assert body["state_topic"].startswith("claude_code_usage/")
        assert body["state_topic"].endswith("/state")
        assert body["value_template"] == "{{ value_json.value }}"
        assert body["availability_topic"] == "claude_code_usage/availability"
        assert body["payload_available"] == "online"
        assert body["payload_not_available"] == "offline"
        assert body["device"]["identifiers"] == ["claude_code_usage"]
        assert body["device"]["name"] == "Claude Code Usage"


def test_mood_sensor_has_enum_options():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    mood = next(c for c in cfgs if c.sensor_id == "mood")
    body = json.loads(mood.payload)
    assert body["device_class"] == "enum"
    assert set(body["options"]) == {"idle", "normal", "active", "heavy"}


def test_session_pct_sensor_has_percent_unit():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    spct = next(c for c in cfgs if c.sensor_id == "session_pct")
    body = json.loads(spct.payload)
    assert body["unit_of_measurement"] == "%"
    assert body["state_class"] == "measurement"


def test_discovery_topic_format():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
        discovery_prefix="homeassistant",
    )
    spct = next(c for c in cfgs if c.sensor_id == "session_pct")
    assert spct.topic == "homeassistant/sensor/claude_code_usage/session_pct/config"


def test_include_account_attribute_adds_json_attributes_topic():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
        include_account_attribute=True,
    )
    for cfg in cfgs:
        body = json.loads(cfg.payload)
        assert body["json_attributes_topic"] == f"claude_code_usage/{cfg.sensor_id}/state"
        assert "account" in body["json_attributes_template"]


def test_no_account_attribute_by_default():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    for cfg in cfgs:
        body = json.loads(cfg.payload)
        assert "json_attributes_topic" not in body
        assert "json_attributes_template" not in body


def test_device_name_flows_into_discovery():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code (Work)",
        base_topic="claude_code_usage",
    )
    for cfg in cfgs:
        body = json.loads(cfg.payload)
        assert body["device"]["name"] == "Claude Code (Work)"


def test_numeric_sensors_have_display_precision():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    by_id = {c.sensor_id: json.loads(c.payload) for c in cfgs}
    # spot-check expected precisions
    assert by_id["session_pct"]["suggested_display_precision"] == 1
    assert by_id["burn_rate_pct_per_min"]["suggested_display_precision"] == 3
    assert by_id["spend_so_far_usd"]["suggested_display_precision"] == 2
    assert by_id["tokens_used"]["suggested_display_precision"] == 0
    # enum sensors must NOT have precision (it's meaningless for strings)
    assert "suggested_display_precision" not in by_id["mood"]
    assert "suggested_display_precision" not in by_id["session_status"]
