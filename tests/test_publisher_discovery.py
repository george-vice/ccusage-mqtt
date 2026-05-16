import json

from ccusage_mqtt.publisher import DiscoveryConfig, build_discovery_configs


def test_returns_15_configs():
    # 14 telemetry sensors + 1 dedicated "Account" sensor that surfaces the
    # account label in the HA device card.
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    assert len(cfgs) == 15
    assert any(c.sensor_id == "account" for c in cfgs)


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


def test_account_attribute_present_by_default():
    """Account is now always emitted (defaults to 'default'), so HA always
    gets the json_attributes wiring without an opt-in flag."""
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    for cfg in cfgs:
        body = json.loads(cfg.payload)
        assert body["json_attributes_topic"] == f"claude_code_usage/{cfg.sensor_id}/state"
        assert "account" in body["json_attributes_template"]


def test_can_suppress_account_attribute_when_explicitly_disabled():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
        include_account_attribute=False,
    )
    for cfg in cfgs:
        body = json.loads(cfg.payload)
        assert "json_attributes_topic" not in body


def test_account_sensor_is_string_with_no_unit():
    cfgs = build_discovery_configs(
        device_id="claude_code_usage",
        device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    account = next(c for c in cfgs if c.sensor_id == "account")
    body = json.loads(account.payload)
    assert body["name"] == "Account"
    assert "unit_of_measurement" not in body
    assert "device_class" not in body
    assert "state_class" not in body
    assert "suggested_display_precision" not in body


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
