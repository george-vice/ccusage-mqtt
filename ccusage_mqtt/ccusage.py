from __future__ import annotations

import json
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
