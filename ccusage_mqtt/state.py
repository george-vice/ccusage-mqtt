from __future__ import annotations

from dataclasses import dataclass, field

from ccusage_mqtt.anthropic_client import RateLimitSnapshot, Status
from ccusage_mqtt.ccusage import BlockSnapshot
from ccusage_mqtt.usage_rate import Mood


@dataclass
class State:
    session_pct: float | None = None
    session_reset_minutes: int | None = None
    session_status: Status = "unknown"
    weekly_pct: float | None = None
    weekly_reset_minutes: int | None = None
    weekly_status: Status = "unknown"

    burn_rate_pct_per_min: float | None = None
    mood: Mood = "idle"
    time_to_limit_minutes: float | None = None
    block_elapsed_pct: float | None = None

    tokens_used: int | None = None
    tokens_per_hour: float | None = None
    spend_so_far_usd: float | None = None
    spend_per_hour_usd: float | None = None

    # Internal — not published.
    block_elapsed_minutes: float | None = field(default=None, repr=False)

    def apply_rate_limits(self, snap: RateLimitSnapshot) -> None:
        self.session_pct = snap.session_pct
        self.session_reset_minutes = snap.session_reset_minutes
        self.session_status = snap.session_status
        self.weekly_pct = snap.weekly_pct
        self.weekly_reset_minutes = snap.weekly_reset_minutes
        self.weekly_status = snap.weekly_status

    def apply_block(self, snap: BlockSnapshot) -> None:
        self.tokens_used = snap.tokens_used
        self.spend_so_far_usd = snap.spend_so_far_usd
        self.block_elapsed_minutes = snap.block_elapsed_minutes

    def mark_headers_stale(self) -> None:
        self.session_status = "unknown"
        self.weekly_status = "unknown"
