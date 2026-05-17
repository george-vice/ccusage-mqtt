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
    # True when the response uses the Enterprise header schema (no 5h/7d window,
    # only overage-* fields). The publisher uses this to switch mood
    # classification from burn-rate to tokens/hour, since overage-utilization is
    # 0 for users still inside their base allocation — making burn-rate useless
    # as an activity signal on Enterprise.
    is_enterprise: bool = False


def _get_ci(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return None


def _first_present(headers: Mapping[str, str], *names: str) -> str | None:
    for n in names:
        v = _get_ci(headers, n)
        if v is not None:
            return v
    return None


def _parse_pct(headers: Mapping[str, str], *names: str) -> float | None:
    """Try each header name in order; parse the first present one.

    Pro/Max accounts return `…-5h-utilization` / `…-7d-utilization`. Enterprise
    accounts return `…-overage-utilization` instead (no 5h / 7d windows). The
    fallback list lets one parser handle both schemas.
    """
    raw = _first_present(headers, *names)
    if raw is None:
        return None
    try:
        # Round to avoid IEEE-754 noise like `0.07 * 100 == 7.000000000000001`.
        return round(float(raw) * 100.0, 4)
    except ValueError:
        return None


def _parse_reset_minutes(headers: Mapping[str, str], *names: str, now: datetime) -> int | None:
    """Anthropic returns reset timestamps as Unix epoch seconds in a string."""
    raw = _first_present(headers, *names)
    if raw is None:
        return None
    try:
        reset_unix = float(raw)
    except ValueError:
        return None
    delta_sec = reset_unix - now.timestamp()
    return max(0, round(delta_sec / 60))


def _parse_status(headers: Mapping[str, str], *names: str) -> Status:
    raw = _first_present(headers, *names)
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
    # session_* maps to the binding 5h/burst-style limit on Pro/Max accounts,
    # and to the overage-billing limit on Enterprise accounts (which have no
    # 5h window). weekly_* is Pro/Max-only; Enterprise leaves it null.
    has_5h = _get_ci(headers, "anthropic-ratelimit-unified-5h-utilization") is not None
    has_overage = _get_ci(headers, "anthropic-ratelimit-unified-overage-utilization") is not None
    return RateLimitSnapshot(
        session_pct=_parse_pct(
            headers,
            "anthropic-ratelimit-unified-5h-utilization",
            "anthropic-ratelimit-unified-overage-utilization",
        ),
        session_reset_minutes=_parse_reset_minutes(
            headers,
            "anthropic-ratelimit-unified-5h-reset",
            "anthropic-ratelimit-unified-overage-reset",
            "anthropic-ratelimit-unified-reset",
            now=now,
        ),
        session_status=_parse_status(
            headers,
            "anthropic-ratelimit-unified-5h-status",
            "anthropic-ratelimit-unified-overage-status",
            "anthropic-ratelimit-unified-status",
        ),
        weekly_pct=_parse_pct(headers, "anthropic-ratelimit-unified-7d-utilization"),
        weekly_reset_minutes=_parse_reset_minutes(
            headers, "anthropic-ratelimit-unified-7d-reset", now=now,
        ),
        weekly_status=_parse_status(headers, "anthropic-ratelimit-unified-7d-status"),
        is_enterprise=has_overage and not has_5h,
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
