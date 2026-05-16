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
        "CLAUDE_CREDENTIALS_PATH": "/custom/creds.json",
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
    assert cfg.claude_credentials_path == "/custom/creds.json"
    assert cfg.header_poll_sec == 60.0
    assert cfg.mood_active_below == 0.33
    assert cfg.log_level == "DEBUG"


def test_load_config_uses_defaults():
    cfg = load_config_from_env({"MQTT_HOST": "broker"})
    assert cfg.mqtt_port == 1883
    assert cfg.mqtt_discovery_prefix == "homeassistant"
    assert cfg.mqtt_base_topic == "claude_code_usage"
    assert cfg.mqtt_device_name == "Claude Code Usage"
    assert cfg.account_name is None
    # The credentials path defaults to <claude_dir>/.credentials.json — the
    # claude_dir comes from either the docker bind-mount (when it exists) or
    # the user's ~/.claude (terminal install). Either way they're linked.
    assert cfg.claude_credentials_path.endswith("/.credentials.json")
    assert cfg.claude_credentials_path.startswith(cfg.ccusage_projects_dir)
    assert cfg.header_poll_sec == 60.0
    assert cfg.ccusage_poll_sec == 30.0
    assert cfg.burn_rate_window_sec == 240.0
    assert cfg.mood_idle_below == 0.10
    assert cfg.log_level == "INFO"


def test_load_config_custom_device_name_and_account():
    cfg = load_config_from_env({
        "MQTT_HOST": "broker",
        "MQTT_DEVICE_NAME": "Claude Code (Work)",
        "ACCOUNT_NAME": "work",
    })
    assert cfg.mqtt_device_name == "Claude Code (Work)"
    assert cfg.account_name == "work"


def test_load_config_empty_account_becomes_none():
    cfg = load_config_from_env({"MQTT_HOST": "broker", "ACCOUNT_NAME": ""})
    assert cfg.account_name is None


def test_claude_paths_default_to_home_dir_when_docker_mount_absent(monkeypatch, tmp_path):
    """Terminal install: no /data/claude-projects on disk → use ~/.claude."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force the docker-mount check to miss by patching the function-local Path.
    # (We can't easily make /data/claude-projects absent in CI either, but the
    # function's first check is `Path('/data/...').is_dir()` — in tests we trust
    # that path does not exist and thus the else branch is taken.)
    from ccusage_mqtt import __main__ as m

    # Sanity: /data/claude-projects should not exist on the test runner.
    assert not (m.Path("/data/claude-projects").is_dir())

    cfg = load_config_from_env({"MQTT_HOST": "broker"})
    assert cfg.ccusage_projects_dir == str(tmp_path / ".claude")
    assert cfg.claude_credentials_path == str(tmp_path / ".claude" / ".credentials.json")


def test_load_env_file(tmp_path, monkeypatch):
    from ccusage_mqtt.__main__ import load_env_file

    envfile = tmp_path / ".env"
    envfile.write_text(
        '# a comment\n'
        'FOO=bar\n'
        'QUOTED="hello world"\n'
        'SINGLE_QUOTED=\'tick\'\n'
        '\n'
        'EMPTY_LINE_OK=yes\n'
        'IGNORED_LINE_NO_EQUALS\n'
    )
    # Ensure none of the keys are pre-set
    for k in ("FOO", "QUOTED", "SINGLE_QUOTED", "EMPTY_LINE_OK"):
        monkeypatch.delenv(k, raising=False)

    n = load_env_file(envfile)
    assert n == 4
    import os
    assert os.environ["FOO"] == "bar"
    assert os.environ["QUOTED"] == "hello world"
    assert os.environ["SINGLE_QUOTED"] == "tick"
    assert os.environ["EMPTY_LINE_OK"] == "yes"
    assert "IGNORED_LINE_NO_EQUALS" not in os.environ


def test_load_env_file_does_not_overwrite_existing(monkeypatch, tmp_path):
    from ccusage_mqtt.__main__ import load_env_file

    envfile = tmp_path / ".env"
    envfile.write_text("PREEXISTING=from-file\n")
    monkeypatch.setenv("PREEXISTING", "from-shell")

    n = load_env_file(envfile)
    assert n == 0
    import os
    assert os.environ["PREEXISTING"] == "from-shell"


def test_load_env_file_missing_path_is_noop(tmp_path):
    from ccusage_mqtt.__main__ import load_env_file

    n = load_env_file(tmp_path / "does-not-exist.env")
    assert n == 0


def test_load_config_requires_mqtt_host():
    with pytest.raises(SystemExit, match="MQTT_HOST"):
        load_config_from_env({})
