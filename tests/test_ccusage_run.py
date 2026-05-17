from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from ccusage_mqtt.ccusage import CcusageError, run


SAMPLE_STDOUT = """
{"blocks":[{"id":"b","startTime":"2026-05-16T15:00:00Z","endTime":"2026-05-16T20:00:00Z","isActive":true,"tokenCounts":{"inputTokens":100,"outputTokens":200,"cacheCreationInputTokens":0,"cacheReadInputTokens":0},"costUSD":0.01,"models":[]}]}
""".strip()


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_invokes_ccusage_with_correct_args(mock_subprocess):
    mock_subprocess.return_value = MagicMock(returncode=0, stdout=SAMPLE_STDOUT, stderr="")
    snap = run(projects_dir="/data/claude-projects", timeout_sec=10.0,
              now=datetime(2026, 5, 16, 16, 0, 0, tzinfo=timezone.utc))
    assert snap is not None
    assert snap.tokens_used == 300

    args = mock_subprocess.call_args
    cmd = args.kwargs.get("args") or args.args[0]
    assert cmd[:3] == ["npx", "ccusage", "blocks"]
    assert "--json" in cmd
    assert "--offline" in cmd
    env = args.kwargs.get("env") or {}
    assert env.get("CLAUDE_CONFIG_DIR") == "/data/claude-projects"


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_raises_on_nonzero_exit(mock_subprocess):
    mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(CcusageError, match="exit code 1"):
        run(projects_dir="/data/claude-projects", timeout_sec=10.0)


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_raises_on_garbage_stdout(mock_subprocess):
    mock_subprocess.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
    with pytest.raises(CcusageError, match="malformed JSON"):
        run(projects_dir="/data/claude-projects", timeout_sec=10.0)


@patch("ccusage_mqtt.ccusage.subprocess.run")
def test_run_raises_on_timeout(mock_subprocess):
    import subprocess as sp
    mock_subprocess.side_effect = sp.TimeoutExpired(cmd="ccusage", timeout=10.0)
    with pytest.raises(CcusageError, match="timed out"):
        run(projects_dir="/data/claude-projects", timeout_sec=10.0)
