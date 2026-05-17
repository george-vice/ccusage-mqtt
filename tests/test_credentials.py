import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

from ccusage_mqtt.credentials import (
    ClaudeCredentials,
    CredentialsMalformed,
    CredentialsMissing,
    CredentialsRefreshFailed,
    load_claude_credentials,
    refresh_claude_credentials,
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


# ---------- refresh_claude_credentials ----------------------------------------


@patch("ccusage_mqtt.credentials.requests.post")
def test_refresh_writes_new_tokens_back_to_file(mock_post, tmp_path: Path):
    p = _write(tmp_path, {
        "claudeAiOauth": {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": 1000,
            "subscriptionType": "max",  # ensure unrelated fields are preserved
        },
        "mcpOAuth": {"keepme": True},  # ensure top-level siblings preserved
    })
    p.chmod(0o600)

    mock_post.return_value = MagicMock(
        ok=True,
        status_code=200,
        json=lambda: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 28800,
            "token_type": "Bearer",
        },
    )

    creds = refresh_claude_credentials(p)
    assert creds.access_token == "new-access"

    # File content updated in place, preserving other fields
    written = json.loads(p.read_text())
    assert written["claudeAiOauth"]["accessToken"] == "new-access"
    assert written["claudeAiOauth"]["refreshToken"] == "new-refresh"
    assert written["claudeAiOauth"]["expiresAt"] > 1_700_000_000_000  # roughly "now in ms"
    assert written["claudeAiOauth"]["subscriptionType"] == "max"   # preserved
    assert written["mcpOAuth"] == {"keepme": True}                  # preserved

    # File mode preserved
    assert (p.stat().st_mode & 0o777) == 0o600

    # The POST went to the right endpoint with the expected payload
    call = mock_post.call_args
    assert call.args[0] == "https://platform.claude.com/v1/oauth/token"
    body = call.kwargs["json"]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "old-refresh"
    assert body["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


@patch("ccusage_mqtt.credentials.requests.post")
def test_refresh_keeps_old_refresh_token_if_response_omits_one(mock_post, tmp_path: Path):
    p = _write(tmp_path, {"claudeAiOauth": {"accessToken": "x", "refreshToken": "old-refresh"}})
    mock_post.return_value = MagicMock(
        ok=True, status_code=200,
        json=lambda: {"access_token": "new-access", "expires_in": 3600},
    )
    refresh_claude_credentials(p)
    written = json.loads(p.read_text())
    assert written["claudeAiOauth"]["refreshToken"] == "old-refresh"


@patch("ccusage_mqtt.credentials.requests.post")
def test_refresh_raises_on_non_2xx(mock_post, tmp_path: Path):
    p = _write(tmp_path, {"claudeAiOauth": {"accessToken": "x", "refreshToken": "r"}})
    mock_post.return_value = MagicMock(ok=False, status_code=400, text='{"error":"invalid_grant"}')
    with pytest.raises(CredentialsRefreshFailed, match="400"):
        refresh_claude_credentials(p)


@patch("ccusage_mqtt.credentials.requests.post")
def test_refresh_raises_on_network_error(mock_post, tmp_path: Path):
    p = _write(tmp_path, {"claudeAiOauth": {"accessToken": "x", "refreshToken": "r"}})
    mock_post.side_effect = requests.RequestException("boom")
    with pytest.raises(CredentialsRefreshFailed, match="network error"):
        refresh_claude_credentials(p)


def test_refresh_raises_malformed_if_no_refresh_token(tmp_path: Path):
    p = _write(tmp_path, {"accessToken": "x"})  # no refreshToken
    with pytest.raises(CredentialsMalformed, match="refreshToken"):
        refresh_claude_credentials(p)


def test_refresh_raises_missing_if_no_file(tmp_path: Path):
    with pytest.raises(CredentialsMissing):
        refresh_claude_credentials(tmp_path / "nope.json")


@patch("ccusage_mqtt.credentials.requests.post")
def test_refresh_raises_if_response_missing_access_token(mock_post, tmp_path: Path):
    p = _write(tmp_path, {"claudeAiOauth": {"accessToken": "x", "refreshToken": "r"}})
    mock_post.return_value = MagicMock(ok=True, status_code=200, json=lambda: {"expires_in": 3600})
    with pytest.raises(CredentialsRefreshFailed, match="access_token"):
        refresh_claude_credentials(p)
