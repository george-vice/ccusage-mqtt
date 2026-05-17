from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class BlockSnapshot:
    tokens_used: int
    spend_so_far_usd: float
    block_started_at: datetime
    block_ends_at: datetime
    block_elapsed_minutes: float


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_blocks_json(raw: str, *, now: datetime | None = None) -> BlockSnapshot | None:
    """Parse `ccusage blocks --json` stdout. Returns the active block or None.

    Raises ValueError on malformed JSON (subprocess gave us garbage).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    data = json.loads(raw)  # ValueError on malformed
    blocks = data.get("blocks") or []
    active = next((b for b in blocks if b.get("isActive")), None)
    if active is None:
        return None

    tc = active.get("tokenCounts") or {}
    tokens = (
        int(tc.get("inputTokens", 0))
        + int(tc.get("outputTokens", 0))
        + int(tc.get("cacheCreationInputTokens", 0))
        + int(tc.get("cacheReadInputTokens", 0))
    )
    started = _parse_iso(active["startTime"])
    ends = _parse_iso(active["endTime"])
    elapsed_min = max(0.0, (now - started).total_seconds() / 60.0)

    return BlockSnapshot(
        tokens_used=tokens,
        spend_so_far_usd=float(active.get("costUSD", 0.0)),
        block_started_at=started,
        block_ends_at=ends,
        block_elapsed_minutes=elapsed_min,
    )


class CcusageError(Exception):
    """Recoverable — caller should keep last-known token/spend values."""


def run(
    *,
    projects_dir: str,
    timeout_sec: float,
    now: datetime | None = None,
) -> BlockSnapshot | None:
    """Invoke `npx ccusage blocks --json --offline` against projects_dir.

    Returns the active block snapshot, or None if no active block.
    Raises CcusageError on subprocess failure or malformed output.
    """
    env = {**os.environ, "CLAUDE_CONFIG_DIR": projects_dir}
    try:
        result = subprocess.run(
            args=["npx", "ccusage", "blocks", "--json", "--offline"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CcusageError(f"ccusage timed out after {timeout_sec}s") from e
    if result.returncode != 0:
        raise CcusageError(f"ccusage exit code {result.returncode}: {result.stderr[:200]}")
    try:
        return parse_blocks_json(result.stdout, now=now)
    except ValueError as e:
        raise CcusageError(f"ccusage produced malformed JSON: {e}") from e
