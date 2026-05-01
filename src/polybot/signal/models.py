"""Signal-layer data carriers.

All types are Pydantic-validated where they cross module boundaries; small
internal records use slots-dataclasses for speed. Decimals throughout for
price math; ints for ms timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Trend, pivots
# ============================================================================


class Trend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEW_TREND_PENDING = "NEW_TREND_PENDING"   # cold-start: no SLQ yet


class PivotType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(slots=True, frozen=True)
class Pivot:
    """A swing high or low. Color-independent — only OHLC values matter."""

    index: int                  # index into the candle sequence
    timestamp_ms: int
    price: Decimal
    type: PivotType
    is_confirmed: bool          # False for tentative; True for confirmed (lookback closed)


# ============================================================================
# Liquidity hierarchy (§6.2.2)
# ============================================================================


class NodeType(str, Enum):
    MAIN = "MAIN"
    SLQ = "SLQ"
    TLQ = "TLQ"
    ILQ = "ILQ"


class EfficiencyStatus(str, Enum):
    EFFICIENT = "efficient"
    INEFFICIENT = "inefficient"
    PENDING = "pending"
    PRE_EFFICIENT = "pre-efficient"


@dataclass(slots=True)
class LiquidityNode:
    price: Decimal
    timestamp_ms: int
    node_type: NodeType
    is_static: bool
    direction: PivotType                       # whether the underlying pivot is a HIGH or LOW
    formed_at_pivot_index: int
    efficiency_status: EfficiencyStatus = EfficiencyStatus.PENDING
    swept_at_ms: int | None = None
    bridge_filled_at_ms: int | None = None
    node_id: str = ""                          # filled at construction by the hierarchy

    def __post_init__(self) -> None:
        if not self.node_id:
            self.node_id = (
                f"{self.node_type.value}-{self.direction.value}-"
                f"{self.timestamp_ms}-{self.formed_at_pivot_index}"
            )


# ============================================================================
# Efficiency state (§6.2.3)
# ============================================================================


class EfficiencyState(str, Enum):
    EPA_C2 = "EPA_C2"
    EPA_C1 = "EPA_C1"
    IPA_FROZEN = "IPA_FROZEN"
    EFFICIENT = "EFFICIENT"


# ============================================================================
# Sweep / bridge (§6.2.4)
# ============================================================================


class SweepType(str, Enum):
    ILQ = "ILQ"
    SLQ = "SLQ"


@dataclass(slots=True, frozen=True)
class SweepEvent:
    type: SweepType
    swept_node_id: str
    swept_at_ms: int
    swept_price: Decimal
    validated_immediately: bool                # True for SLQ or pre-efficient ILQ
    requires_bridge: bool                      # True iff ILQ that needs EfficiencyBridge


# ============================================================================
# Malaysian SnD zones (§6.2.5, §1.5.5)
# ============================================================================


class SndPattern(str, Enum):
    INSIDE_BAR = "inside_bar"
    DOJI = "doji"
    DBD = "dbd"
    RBD = "rbd"
    SND_GAP = "snd_gap"
    APEX = "apex"                # Tier-B (disabled)
    A_SHAPE = "a_shape"          # Tier-B
    LEFT_SHOULDER = "left_shoulder"  # Tier-B
    SBR = "sbr"                  # Tier-B


@dataclass(slots=True)
class SnDZone:
    top: Decimal
    bottom: Decimal
    structure_type: SndPattern
    direction: Literal["SUPPLY", "DEMAND"]    # supply = downtrend zone (sell side)
    formed_at_ms: int
    source_candle_indices: tuple[int, ...]
    formation_volume_ratio: Decimal           # vs trailing average; 1.0 if disabled
    pattern_confidence: Decimal               # [0, 1]
    half_life_min: Decimal                    # for freshness decay
    max_age_min: Decimal                      # hard expiry
    zone_id: str = ""

    def __post_init__(self) -> None:
        if self.bottom > self.top:
            raise ValueError(f"SnDZone: bottom > top ({self.bottom} > {self.top})")
        if not (Decimal(0) <= self.pattern_confidence <= Decimal(1)):
            raise ValueError(
                f"SnDZone: pattern_confidence must be in [0,1]; got {self.pattern_confidence}"
            )
        if not self.zone_id:
            self.zone_id = (
                f"{self.structure_type.value}-{self.direction}-{self.formed_at_ms}"
            )

    def freshness_at(self, ts_ms: int) -> Decimal:
        """Exponentially-decaying freshness multiplier per §1.5.5.

        Half-life semantic: at age = half_life_min, freshness = 0.5.
        Formula: freshness = 2 ** (-age_min / half_life_min)  (equivalent to
        exp(-ln(2) * age_min / half_life_min)).
        """
        from decimal import Decimal as D
        import math

        age_ms = max(0, ts_ms - self.formed_at_ms)
        age_min = D(age_ms) / D(60_000)
        if self.half_life_min <= 0:
            return D(1)
        ratio = float(age_min) / float(self.half_life_min)
        return D(str(2.0 ** (-ratio)))

    def is_expired(self, ts_ms: int) -> bool:
        from decimal import Decimal as D
        age_ms = max(0, ts_ms - self.formed_at_ms)
        age_min = D(age_ms) / D(60_000)
        return age_min > self.max_age_min


# ============================================================================
# Setup types and signals (§6.2.8)
# ============================================================================


class SetupType(str, Enum):
    DBD_ILQ = "DBD-ILQ"
    RBD_ILQ = "RBD-ILQ"
    DBD_SLQ = "DBD-SLQ"
    RBD_SLQ = "RBD-SLQ"
    DOJI_ILQ = "DOJI-ILQ"
    INSIDE_ILQ = "INSIDE-ILQ"
    SND_GAP_ILQ = "SND_GAP-ILQ"


class SignalDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class DepthBucket(str, Enum):
    SHALLOW = "shallow"
    MEDIUM = "medium"
    DEEP = "deep"


class Signal(BaseModel):
    """The output of the PA engine on Alert-3 confirmation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    direction: SignalDirection
    setup_type: SetupType
    depth_bucket: DepthBucket
    confidence: Decimal = Field(ge=0, le=1)
    continuation_prior: Decimal = Field(ge=0, le=1)
    snd_zone_id: str
    invalidation_level: Decimal
    expires_at_slot_end_ms: int
    confirmation_age_ms: int
    rejection_depth_bps: Decimal
    timestamp_ms: int
    rationale: str = ""

    # Diagnostic breakdown of the confidence formula (§6.2.8)
    freshness_factor: Decimal = Field(ge=0, le=1)
    htf_alignment_factor: Decimal = Field(ge=0, le=1)
    volume_filter_factor: Decimal = Field(ge=0, le=2)   # may be > 1 for boost
    pattern_confidence: Decimal = Field(ge=0, le=1)


# ============================================================================
# Alerts (§6.2.10)
# ============================================================================


class AlertType(str, Enum):
    PRE_ALERT = "alert1"
    EARLY_WARNING = "alert2"
    EXECUTION = "alert3"


@dataclass(slots=True, frozen=True)
class AlertPayload:
    type: AlertType
    timestamp_ms: int
    payload: dict[str, object]


# ============================================================================
# Invalidation (§6.2.7)
# ============================================================================


class InvalidationType(str, Enum):
    STANDARD = "standard"
    ABSOLUTE_KILL_SWITCH = "absolute_kill_switch"
    DYNAMIC_STRUCTURAL = "dynamic_structural"
    ORIGIN = "origin"
    MACRO_CYCLE_RESET = "macro_cycle_reset"
    IPA_HALT = "ipa_halt"


@dataclass(slots=True, frozen=True)
class InvalidationEvent:
    type: InvalidationType
    timestamp_ms: int
    triggered_at_price: Decimal
    rationale: str


# ============================================================================
# Continuation filter result (§1.5.1 mitigation)
# ============================================================================


@dataclass(slots=True, frozen=True)
class ContinuationCheckResult:
    passed: bool
    close_open_agreement: bool
    penetration_bps_ok: bool
    tick_slope_agreement_ok: bool
    penetration_bps: Decimal
    tick_slope_agreement_fraction: Decimal


# ============================================================================
# Engine state snapshot (logging)
# ============================================================================


@dataclass(slots=True)
class PAState:
    trend: Trend = Trend.NEW_TREND_PENDING
    main_high: LiquidityNode | None = None
    main_low: LiquidityNode | None = None
    slq: LiquidityNode | None = None
    tlq: LiquidityNode | None = None
    ilq: LiquidityNode | None = None
    efficiency_state: EfficiencyState = EfficiencyState.EFFICIENT
    consecutive_misses: int = 0
    confirmed_pivots: list[Pivot] = field(default_factory=list)
    tentative_pivots: list[Pivot] = field(default_factory=list)
    active_zones: list[SnDZone] = field(default_factory=list)
    latest_signal: Signal | None = None
    latest_invalidation: InvalidationEvent | None = None
    last_alerts_emitted: dict[AlertType, int] = field(default_factory=dict)  # ts of last emit
