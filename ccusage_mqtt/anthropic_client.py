from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping

import requests

# Required to pair an OAuth bearer token with the Messages API. Mirrors
# Clawdmeter/daemon/claude_usage_daemon.py:42.
_OAUTH_BETA = "oauth-2025-04-20"
_ANTHROPIC_VERSION = "2023-06-01"
_USER_AGENT = "claude-code/2.1.5"

Status = Literal["allowed", "limited", "unknown"]


@dataclass(frozen=True)
class RateLimitSnapshot:
    session_pct: float | None
    session_reset_minutes: int | None
    session_status: Status
    weekly_pct: float | None
    weekly_reset_minutes: int | None
    weekly_status: Status


def _get_ci(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return None


def _parse_pct(headers: Mapping[str, str], name: str) -> float | None:
    raw = _get_ci(headers, name)
    if raw is None:
        return None
    try:
        return float(raw) * 100.0
    except ValueError:
        return None


def _parse_reset_minutes(headers: Mapping[str, str], name: str, *, now: datetime) -> int | None:
    """Anthropic returns reset timestamps as Unix epoch seconds in a string.

    (Not ISO 8601 — verified against the Clawdmeter daemon which reads the
    same headers from real responses.)
    """
    raw = _get_ci(headers, name)
    if raw is None:
        return None
    try:
        reset_unix = float(raw)
    except ValueError:
        return None
    delta_sec = reset_unix - now.timestamp()
    return max(0, round(delta_sec / 60))


def _parse_status(headers: Mapping[str, str], name: str) -> Status:
    raw = _get_ci(headers, name)
    if raw in ("allowed", "limited"):
        return raw
    return "unknown"


def parse_ratelimit_headers(
    headers: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> RateLimitSnapshot:
    if now is None:
        now = datetime.now(timezone.utc)
    return RateLimitSnapshot(
        session_pct=_parse_pct(headers, "anthropic-ratelimit-unified-5h-utilization"),
        session_reset_minutes=_parse_reset_minutes(headers, "anthropic-ratelimit-unified-5h-reset", now=now),
        session_status=_parse_status(headers, "anthropic-ratelimit-unified-5h-status"),
        weekly_pct=_parse_pct(headers, "anthropic-ratelimit-unified-7d-utilization"),
        weekly_reset_minutes=_parse_reset_minutes(headers, "anthropic-ratelimit-unified-7d-reset", now=now),
        weekly_status=_parse_status(headers, "anthropic-ratelimit-unified-7d-status"),
    )


class AnthropicProbeError(Exception):
    """Recoverable error — caller should keep going."""


class AnthropicAuthError(AnthropicProbeError):
    """Fatal — bad credentials. Caller should exit non-zero."""


class AnthropicRateLimited(AnthropicProbeError):
    """The probe itself was 429'd. Headers may still be present and useful."""
    def __init__(self, snapshot: RateLimitSnapshot) -> None:
        super().__init__("rate limited")
        self.snapshot = snapshot


def probe(
    *,
    access_token: str,
    api_base: str,
    model: str,
    timeout_sec: float,
    now: datetime | None = None,
) -> RateLimitSnapshot:
    """POST /v1/messages with the smallest valid OAuth-authed body; return parsed headers.

    Uses the Claude Code OAuth bearer flow (anthropic-beta: oauth-2025-04-20)
    so the response headers reflect the same 5h/7d window Claude Code uses.
    See Clawdmeter/daemon/claude_usage_daemon.py:40-50.

    Raises:
        AnthropicAuthError: 401 or 403 — token is wrong/expired, fatal.
        AnthropicRateLimited: 429 — back off and keep going.
        AnthropicProbeError: any other non-2xx, or network failure.
    """
    url = api_base.rstrip("/") + "/v1/messages"
    body = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    request_headers = {
        "Authorization": f"Bearer {access_token}",
        "anthropic-version": _ANTHROPIC_VERSION,
        "anthropic-beta": _OAUTH_BETA,
        "content-type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    try:
        resp = requests.post(url, headers=request_headers, json=body, timeout=timeout_sec)
    except requests.RequestException as e:
        raise AnthropicProbeError(f"network error: {e}") from e

    if resp.status_code in (401, 403):
        raise AnthropicAuthError(f"{resp.status_code} from {url}: {resp.text[:200]}")

    if resp.status_code == 429:
        snap = parse_ratelimit_headers(resp.headers, now=now)
        raise AnthropicRateLimited(snap)

    if not resp.ok:
        raise AnthropicProbeError(f"{resp.status_code} from {url}: {resp.text[:200]}")

    return parse_ratelimit_headers(resp.headers, now=now)
