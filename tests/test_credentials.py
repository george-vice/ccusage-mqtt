import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ccusage_mqtt.credentials import (
    ClaudeCredentials,
    CredentialsMalformed,
    CredentialsMissing,
    load_claude_credentials,
)


_NOW = datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc)


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / ".credentials.json"
    p.write_text(json.dumps(payload))
    return p


def test_loads_nested_claude_oauth_shape(tmp_path: Path):
    expires_ms = int((_NOW + timedelta(days=30)).timestamp() * 1000)
    p = _write(tmp_path, {
        "claudeAiOauth": {
            "accessToken": "oauth-abc",
            "expiresAt": expires_ms,
            "refreshToken": "refresh-xyz",
        },
        "mcpOAuth": {},
    })
    creds = load_claude_credentials(p)
    assert creds.access_token == "oauth-abc"
    assert creds.expires_at is not None
    assert creds.expires_at.tzinfo is not None
    assert not creds.is_expired(now=_NOW)


def test_loads_flat_shape(tmp_path: Path):
    p = _write(tmp_path, {"accessToken": "flat-token", "expiresAt": 9999999999999})
    creds = load_claude_credentials(p)
    assert creds.access_token == "flat-token"


def test_missing_file_raises_missing(tmp_path: Path):
    with pytest.raises(CredentialsMissing):
        load_claude_credentials(tmp_path / "does-not-exist.json")


def test_malformed_file_raises_malformed(tmp_path: Path):
    p = tmp_path / ".credentials.json"
    p.write_text('{"unrelated": "structure"}')
    with pytest.raises(CredentialsMalformed):
        load_claude_credentials(p)


def test_empty_file_raises_malformed(tmp_path: Path):
    p = tmp_path / ".credentials.json"
    p.write_text("")
    with pytest.raises(CredentialsMalformed):
        load_claude_credentials(p)


def test_is_expired_when_past(tmp_path: Path):
    past_ms = int((_NOW - timedelta(minutes=1)).timestamp() * 1000)
    p = _write(tmp_path, {"claudeAiOauth": {"accessToken": "t", "expiresAt": past_ms}})
    creds = load_claude_credentials(p)
    assert creds.is_expired(now=_NOW)


def test_is_expired_within_leeway(tmp_path: Path):
    near_ms = int((_NOW + timedelta(seconds=30)).timestamp() * 1000)
    p = _write(tmp_path, {"claudeAiOauth": {"accessToken": "t", "expiresAt": near_ms}})
    creds = load_claude_credentials(p)
    assert creds.is_expired(now=_NOW, leeway_sec=60)
    assert not creds.is_expired(now=_NOW, leeway_sec=15)


def test_unknown_expiry_is_not_expired(tmp_path: Path):
    p = _write(tmp_path, {"accessToken": "t"})  # no expiresAt
    creds = load_claude_credentials(p)
    assert creds.expires_at is None
    assert not creds.is_expired(now=_NOW)


def test_returned_dataclass_is_frozen(tmp_path: Path):
    p = _write(tmp_path, {"accessToken": "t"})
    creds = load_claude_credentials(p)
    with pytest.raises(Exception):
        creds.access_token = "other"  # type: ignore[misc]
