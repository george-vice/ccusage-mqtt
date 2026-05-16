from __future__ import annotations

from dataclasses import dataclass, field

from ccusage_mqtt.anthropic_client import RateLimitSnapshot, Status
from ccusage_mqtt.ccusage import BlockSnapshot
from ccusage_mqtt.usage_rate import Mood, classify_mood


BLOCK_WINDOW_MINUTES = 300.0  # Anthropic 5h window
RATE_MIN_ELAPSED_MIN = 1.0    # need ≥1 min of block before publishing per-hour rates


@dataclass
class DerivationConfig:
    idle_below: float = 0.10
    normal_below: float = 0.20
    active_below: float = 0.33


SENSOR_FIELDS: tuple[str, ...] = (
    "session_pct",
    "session_reset_minutes",
    "session_status",
    "weekly_pct",
    "weekly_reset_minutes",
    "weekly_status",
    "burn_rate_pct_per_min",
    "mood",
    "time_to_limit_minutes",
    "block_elapsed_pct",
    "tokens_used",
    "tokens_per_hour",
    "spend_so_far_usd",
    "spend_per_hour_usd",
)


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

    def recompute_derived(
        self,
        *,
        burn_rate: float | None,
        cfg: DerivationConfig | None = None,
    ) -> None:
        cfg = cfg or DerivationConfig()
        self.burn_rate_pct_per_min = burn_rate
        self.mood = classify_mood(
            burn_rate,
            idle_below=cfg.idle_below,
            normal_below=cfg.normal_below,
            active_below=cfg.active_below,
        )

        if burn_rate is not None and burn_rate > 0.0 and self.session_pct is not None:
            self.time_to_limit_minutes = (100.0 - self.session_pct) / burn_rate
        else:
            self.time_to_limit_minutes = None

        if self.session_reset_minutes is not None:
            elapsed = BLOCK_WINDOW_MINUTES - self.session_reset_minutes
            self.block_elapsed_pct = max(0.0, min(100.0, elapsed / BLOCK_WINDOW_MINUTES * 100.0))
        else:
            self.block_elapsed_pct = None

        if (
            self.block_elapsed_minutes is not None
            and self.block_elapsed_minutes >= RATE_MIN_ELAPSED_MIN
        ):
            elapsed_h = self.block_elapsed_minutes / 60.0
            self.tokens_per_hour = (self.tokens_used / elapsed_h) if self.tokens_used is not None else None
            self.spend_per_hour_usd = (self.spend_so_far_usd / elapsed_h) if self.spend_so_far_usd is not None else None
        else:
            self.tokens_per_hour = None
            self.spend_per_hour_usd = None

    def to_mqtt_payloads(self) -> dict[str, dict]:
        return {name: {"value": getattr(self, name)} for name in SENSOR_FIELDS}
