import json
from unittest.mock import MagicMock, patch

from ccusage_mqtt.publisher import MqttClient, build_discovery_configs


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_client_sets_will_on_construct(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake

    MqttClient(
        host="broker", port=1883, username=None, password=None,
        client_id="ccusage-mqtt",
        availability_topic="claude_code_usage/availability",
    )

    fake.will_set.assert_called_once_with(
        "claude_code_usage/availability",
        payload="offline",
        qos=1,
        retain=True,
    )


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_publish_discovery_sends_each_config_retained(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="claude_code_usage/availability")

    cfgs = build_discovery_configs(
        device_id="claude_code_usage", device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    client.publish_discovery(cfgs)

    assert fake.publish.call_count == 14
    # Spot-check one call
    call0 = fake.publish.call_args_list[0]
    assert call0.kwargs.get("retain", call0.args[3] if len(call0.args) >= 4 else None) is True


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_publish_state_sends_json_envelope(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="a")

    client.publish_state(base_topic="claude_code_usage",
                         payloads={"session_pct": {"value": 42.0}, "mood": {"value": "idle"}})

    assert fake.publish.call_count == 2
    topics = sorted(c.args[0] for c in fake.publish.call_args_list)
    assert topics == ["claude_code_usage/mood/state", "claude_code_usage/session_pct/state"]
    payloads = sorted(c.args[1] for c in fake.publish.call_args_list)
    assert json.loads(payloads[0]) == {"value": "idle"}
    assert json.loads(payloads[1]) == {"value": 42.0}


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_publish_state_skips_unchanged_values_on_repeat(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="a")

    client.publish_state(base_topic="ct", payloads={"a": {"value": 1}, "b": {"value": 2}})
    fake.publish.reset_mock()
    client.publish_state(base_topic="ct", payloads={"a": {"value": 1}, "b": {"value": 3}})

    # Only 'b' changed
    assert fake.publish.call_count == 1
    assert fake.publish.call_args.args[0] == "ct/b/state"


@patch("ccusage_mqtt.publisher.mqtt.Client")
def test_on_connect_publishes_online_and_rediscovers(mock_mqtt_cls):
    fake = MagicMock()
    mock_mqtt_cls.return_value = fake
    client = MqttClient(host="broker", port=1883, username=None, password=None,
                       client_id="x", availability_topic="claude_code_usage/availability")
    cfgs = build_discovery_configs(
        device_id="claude_code_usage", device_name="Claude Code Usage",
        base_topic="claude_code_usage",
    )
    client.set_discovery_configs(cfgs)

    # Simulate paho-mqtt invoking the on_connect callback (signature differs by paho version;
    # we use Callback API v2 → on_connect(client, userdata, flags, reason_code, properties))
    client._on_connect(fake, None, {}, 0, None)

    # Should have: 1 availability=online + 14 discovery publishes
    assert fake.publish.call_count == 15
    topics = [c.args[0] for c in fake.publish.call_args_list]
    assert topics[0] == "claude_code_usage/availability"
    discovery_topics = topics[1:]
    assert all(t.startswith("homeassistant/sensor/claude_code_usage/") for t in discovery_topics)
