"""Read the Claude Code OAuth credentials Anthropic stores in ~/.claude/.credentials.json.

The probe in `anthropic_client` uses these so the `anthropic-ratelimit-unified-*`
headers reflect the Claude Code subscription's 5h/7d window — not a separate
developer-API quota. Mirrors the lookup in
`Clawdmeter/daemon/claude_usage_daemon.py:57-86`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

# Reverse-engineered from Claude Code v2.1.x's bundled binary so the publisher
# can self-refresh expired OAuth tokens without requiring Claude Code to be
# installed in the container (or run by hand on the host). If Anthropic rotates
# either of these, refresh stops working and the publisher falls back to the
# "credentials expired — re-login with `claude`" behavior.
_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


class CredentialsMissing(Exception):
    """The credentials file doesn't exist or isn't readable."""


class CredentialsMalformed(Exception):
    """The file exists but doesn't contain a recognisable access token."""


class CredentialsExpired(Exception):
    """The token's expiresAt is in the past and could not be refreshed."""


class CredentialsRefreshFailed(Exception):
    """The OAuth refresh endpoint rejected the refreshToken.

    Usually means the refresh token itself has expired and the user needs to
    re-login via Claude Code. May also fire if Anthropic rotates the OAuth
    client_id or token endpoint that we hard-code above.
    """


@dataclass(frozen=True)
class ClaudeCredentials:
    access_token: str
    expires_at: datetime | None  # UTC; None if the file didn't record it

    def is_expired(self, *, now: datetime | None = None, leeway_sec: int = 60) -> bool:
        if self.expires_at is None:
            return False  # unknown — let the API tell us via 401
        if now is None:
            now = datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds() < leeway_sec


def _extract_access_token(blob: str) -> str | None:
    blob = blob.strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("accessToken"), str):
            return data["accessToken"]
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                return v["accessToken"]
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    if m:
        return m.group(1)
    return None


def _extract_expires_at(blob: str) -> datetime | None:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for candidate in (data, *(v for v in data.values() if isinstance(v, dict))):
        exp = candidate.get("expiresAt")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp / 1000.0, tz=timezone.utc)
    return None


def load_claude_credentials(path: Path | str) -> ClaudeCredentials:
    p = Path(path)
    try:
        raw = p.read_text()
    except FileNotFoundError as e:
        raise CredentialsMissing(f"credentials file not found: {p}") from e
    except OSError as e:
        raise CredentialsMissing(f"cannot read credentials file {p}: {e}") from e

    token = _extract_access_token(raw)
    if not token:
        raise CredentialsMalformed(f"no accessToken found in {p}")

    return ClaudeCredentials(
        access_token=token,
        expires_at=_extract_expires_at(raw),
    )


def refresh_claude_credentials(
    path: Path | str,
    *,
    timeout_sec: float = 15.0,
) -> ClaudeCredentials:
    """Use the stored refreshToken to mint a new accessToken at Anthropic.

    Writes the rotated tokens back to the credentials file (preserving file
    mode and any unrelated fields) and returns the freshly-loaded
    `ClaudeCredentials`.

    Raises:
        CredentialsMissing: the credentials file doesn't exist.
        CredentialsMalformed: the file exists but has no refreshToken to use.
        CredentialsRefreshFailed: the OAuth endpoint returned a non-2xx, or
            the network call failed. The user needs to re-login with
            `CLAUDE_CONFIG_DIR=<dir> claude` to fix this.
    """
    p = Path(path)
    try:
        raw_text = p.read_text()
    except FileNotFoundError as e:
        raise CredentialsMissing(f"credentials file not found: {p}") from e
    except OSError as e:
        raise CredentialsMissing(f"cannot read credentials file {p}: {e}") from e

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise CredentialsMalformed(f"{p} is not valid JSON: {e}") from e

    # Locate the OAuth node: either nested under `claudeAiOauth` or flat.
    if isinstance(data, dict) and isinstance(data.get("claudeAiOauth"), dict):
        oauth_node = data["claudeAiOauth"]
    elif isinstance(data, dict):
        oauth_node = data
    else:
        raise CredentialsMalformed(f"{p} did not deserialize to an object")

    refresh_token = oauth_node.get("refreshToken")
    if not refresh_token:
        raise CredentialsMalformed(f"no refreshToken in {p}")

    try:
        resp = requests.post(
            _OAUTH_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CLAUDE_CODE_CLIENT_ID,
            },
            timeout=timeout_sec,
        )
    except requests.RequestException as e:
        raise CredentialsRefreshFailed(f"network error calling {_OAUTH_TOKEN_URL}: {e}") from e

    if not resp.ok:
        raise CredentialsRefreshFailed(
            f"{_OAUTH_TOKEN_URL} returned {resp.status_code}: {resp.text[:200]}"
        )

    body = resp.json()
    new_access = body.get("access_token")
    if not new_access:
        raise CredentialsRefreshFailed("oauth response missing access_token")
    # Anthropic rotates the refresh token; keep the old one as a fallback if
    # the response somehow omits the new one.
    new_refresh = body.get("refresh_token", refresh_token)
    expires_in = int(body.get("expires_in", 28800))
    new_expires_at_ms = int((time.time() + expires_in) * 1000)

    # Mutate in-place so we preserve every other field Claude Code writes
    # (subscriptionType, rateLimitTier, scopes, mcpOAuth, ...).
    oauth_node["accessToken"] = new_access
    oauth_node["refreshToken"] = new_refresh
    oauth_node["expiresAt"] = new_expires_at_ms

    mode = p.stat().st_mode
    p.write_text(json.dumps(data, indent=2))
    p.chmod(mode)

    return load_claude_credentials(p)
