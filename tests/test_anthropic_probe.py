from datetime import datetime, timezone

import pytest
import responses

from ccusage_mqtt.anthropic_client import (
    AnthropicAuthError,
    AnthropicProbeError,
    AnthropicRateLimited,
    probe,
)


BASE = "https://api.anthropic.com"
_NOW = datetime(2026, 5, 16, 14, 0, 0, tzinfo=timezone.utc)

HEADERS_OK = {
    "anthropic-ratelimit-unified-5h-utilization": "0.30",
    "anthropic-ratelimit-unified-5h-reset": str(_NOW.timestamp() + 3600),
    "anthropic-ratelimit-unified-5h-status": "allowed",
    "anthropic-ratelimit-unified-7d-utilization": "0.10",
    "anthropic-ratelimit-unified-7d-reset": str(_NOW.timestamp() + 7 * 24 * 3600),
    "anthropic-ratelimit-unified-7d-status": "allowed",
}


@responses.activate
def test_probe_returns_snapshot_on_200():
    responses.add(
        responses.POST,
        f"{BASE}/v1/messages",
        json={"id": "msg_1", "content": []},
        status=200,
        headers=HEADERS_OK,
    )
    snap = probe(
        access_token="oauth-token-1234",
        api_base=BASE,
        model="claude-haiku-4-5-20251001",
        now=_NOW,
        timeout_sec=5.0,
    )
    assert snap.session_pct == 30.0


@responses.activate
def test_probe_raises_auth_error_on_401():
    responses.add(
        responses.POST,
        f"{BASE}/v1/messages",
        json={"error": {"type": "authentication_error", "message": "invalid token"}},
        status=401,
    )
    with pytest.raises(AnthropicAuthError):
        probe(access_token="bad", api_base=BASE, model="m", timeout_sec=5.0)


@responses.activate
def test_probe_raises_auth_error_on_403():
    responses.add(responses.POST, f"{BASE}/v1/messages", json={}, status=403)
    with pytest.raises(AnthropicAuthError):
        probe(access_token="bad", api_base=BASE, model="m", timeout_sec=5.0)


@responses.activate
def test_probe_raises_rate_limited_on_429_and_still_parses_headers():
    responses.add(
        responses.POST,
        f"{BASE}/v1/messages",
        json={"error": {"type": "rate_limit_error"}},
        status=429,
        headers={
            **HEADERS_OK,
            "anthropic-ratelimit-unified-5h-utilization": "0.99",
            "anthropic-ratelimit-unified-5h-status": "limited",
        },
    )
    with pytest.raises(AnthropicRateLimited) as exc_info:
        probe(access_token="oauth-token", api_base=BASE, model="m",
              now=_NOW, timeout_sec=5.0)
    assert exc_info.value.snapshot.session_status == "limited"
    assert exc_info.value.snapshot.session_pct == 99.0


@responses.activate
def test_probe_raises_probe_error_on_5xx():
    responses.add(responses.POST, f"{BASE}/v1/messages", json={}, status=503)
    with pytest.raises(AnthropicProbeError):
        probe(access_token="oauth-token", api_base=BASE, model="m", timeout_sec=5.0)


@responses.activate
def test_probe_request_body_and_headers():
    """Verify OAuth bearer + required beta header + Claude Code UA + 'hi' body."""
    captured = {}
    def callback(request):
        captured["body"] = request.body
        captured["headers"] = dict(request.headers)
        return (200, HEADERS_OK, '{"id":"msg_1","content":[]}')
    responses.add_callback(responses.POST, f"{BASE}/v1/messages", callback=callback)

    probe(access_token="oauth-token-abc", api_base=BASE,
          model="claude-haiku-4-5-20251001", timeout_sec=5.0)

    import json
    body = json.loads(captured["body"])
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["max_tokens"] == 1
    assert body["messages"] == [{"role": "user", "content": "hi"}]

    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "Bearer oauth-token-abc"
    assert "x-api-key" not in headers
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert headers["user-agent"].startswith("claude-code/")
