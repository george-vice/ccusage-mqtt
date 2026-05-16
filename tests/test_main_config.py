import pytest

from ccusage_mqtt.__main__ import AppConfig, load_config_from_env


def test_load_config_from_env_full():
    env = {
        "MQTT_HOST": "10.0.0.1",
        "MQTT_PORT": "8883",
        "MQTT_USER": "u",
        "MQTT_PASS": "p",
        "MQTT_CLIENT_ID": "test-client",
        "MQTT_DISCOVERY_PREFIX": "homeassistant",
        "MQTT_BASE_TOPIC": "claude_code_usage",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_API_BASE": "https://api.anthropic.com",
        "PROBE_MODEL": "claude-haiku-4-5-20251001",
        "CCUSAGE_PROJECTS_DIR": "/data/claude-projects",
        "HEADER_POLL_SEC": "60",
        "CCUSAGE_POLL_SEC": "30",
        "BURN_RATE_WINDOW_SEC": "240",
        "MOOD_IDLE_BELOW": "0.10",
        "MOOD_NORMAL_BELOW": "0.20",
        "MOOD_ACTIVE_BELOW": "0.33",
        "LOG_LEVEL": "DEBUG",
    }
    cfg = load_config_from_env(env)
    assert cfg.mqtt_host == "10.0.0.1"
    assert cfg.mqtt_port == 8883
    assert cfg.mqtt_user == "u"
    assert cfg.anthropic_api_key == "sk-ant-test"
    assert cfg.header_poll_sec == 60.0
    assert cfg.mood_active_below == 0.33
    assert cfg.log_level == "DEBUG"


def test_load_config_uses_defaults():
    env = {
        "MQTT_HOST": "broker",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    cfg = load_config_from_env(env)
    assert cfg.mqtt_port == 1883
    assert cfg.mqtt_discovery_prefix == "homeassistant"
    assert cfg.mqtt_base_topic == "claude_code_usage"
    assert cfg.header_poll_sec == 60.0
    assert cfg.ccusage_poll_sec == 30.0
    assert cfg.burn_rate_window_sec == 240.0
    assert cfg.mood_idle_below == 0.10
    assert cfg.log_level == "INFO"


def test_load_config_requires_mqtt_host():
    with pytest.raises(SystemExit, match="MQTT_HOST"):
        load_config_from_env({"ANTHROPIC_API_KEY": "sk"})


def test_load_config_requires_api_key():
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        load_config_from_env({"MQTT_HOST": "broker"})
