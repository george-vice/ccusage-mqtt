"""Read the Claude Code OAuth credentials Anthropic stores in ~/.claude/.credentials.json.

The probe in `anthropic_client` uses these so the `anthropic-ratelimit-unified-*`
headers reflect the Claude Code subscription's 5h/7d window — not a separate
developer-API quota. Mirrors the lookup in
`Clawdmeter/daemon/claude_usage_daemon.py:57-86`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class CredentialsMissing(Exception):
    """The credentials file doesn't exist or isn't readable."""


class CredentialsMalformed(Exception):
    """The file exists but doesn't contain a recognisable access token."""


class CredentialsExpired(Exception):
    """The token's expiresAt is in the past. Run Claude Code to refresh it."""


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
