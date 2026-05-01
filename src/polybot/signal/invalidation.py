"""Invalidation kill-switches (§6.2.7).

Six predicates, each a pure function returning Optional[InvalidationEvent].
The first to fire produces the event consumed by the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from polybot.signal import math as M
from polybot.signal.models import (
    EfficiencyState,
    InvalidationEvent,
    InvalidationType,
    LiquidityNode,
    Trend,
)


@dataclass(frozen=True)
class InvalidationContext:
    """Inputs the predicates need; constructed by the engine each candle close."""

    direction: Trend
    candle_close: Decimal
    candle_high: Decimal
    candle_low: Decimal
    candle_ts_ms: int
    main_high: LiquidityNode | None
    main_low: LiquidityNode | None
    slq: LiquidityNode | None
    ail: LiquidityNode | None      # Above-ILQ structural high (downtrend) / mirror
    is_young_trend: bool
    efficiency_state: EfficiencyState
    consecutive_misses: int
    consecutive_break_count_no_entry: int
    basis_bps: int = 1


def standard_invalidation(ctx: InvalidationContext) -> InvalidationEvent | None:
    """Close above SLQ kills ILQ setup (downtrend); close below SLQ for uptrend."""
    if ctx.slq is None:
        return None
    if ctx.direction is Trend.DOWN and M.broken(
        ctx.slq.price, "up", ctx.candle_close, basis_bps=ctx.basis_bps
    ):
        return InvalidationEvent(
            type=InvalidationType.STANDARD,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"close {ctx.candle_close} > SLQ {ctx.slq.price} + buffer (downtrend ILQ setup killed)",
        )
    if ctx.direction is Trend.UP and M.broken(
        ctx.slq.price, "down", ctx.candle_close, basis_bps=ctx.basis_bps
    ):
        return InvalidationEvent(
            type=InvalidationType.STANDARD,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"close {ctx.candle_close} < SLQ {ctx.slq.price} (uptrend ILQ setup killed)",
        )
    return None


def absolute_kill_switch(ctx: InvalidationContext) -> InvalidationEvent | None:
    """Close above Main High kills the macro trend (downtrend); mirror for uptrend."""
    if ctx.direction is Trend.DOWN and ctx.main_high is not None and M.broken(
        ctx.main_high.price, "up", ctx.candle_close, basis_bps=ctx.basis_bps
    ):
        return InvalidationEvent(
            type=InvalidationType.ABSOLUTE_KILL_SWITCH,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"close {ctx.candle_close} > Main High {ctx.main_high.price} (macro reversal)",
        )
    if ctx.direction is Trend.UP and ctx.main_low is not None and M.broken(
        ctx.main_low.price, "down", ctx.candle_close, basis_bps=ctx.basis_bps
    ):
        return InvalidationEvent(
            type=InvalidationType.ABSOLUTE_KILL_SWITCH,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"close {ctx.candle_close} < Main Low {ctx.main_low.price} (macro reversal)",
        )
    return None


def dynamic_structural_invalidation(ctx: InvalidationContext) -> InvalidationEvent | None:
    """Close above AIL (nearest structural high above swept ILQ) kills setup."""
    if ctx.ail is None:
        return None
    if ctx.direction is Trend.DOWN and M.broken(
        ctx.ail.price, "up", ctx.candle_close, basis_bps=ctx.basis_bps
    ):
        return InvalidationEvent(
            type=InvalidationType.DYNAMIC_STRUCTURAL,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"close {ctx.candle_close} > AIL {ctx.ail.price} (dynamic structural fail)",
        )
    if ctx.direction is Trend.UP and M.broken(
        ctx.ail.price, "down", ctx.candle_close, basis_bps=ctx.basis_bps
    ):
        return InvalidationEvent(
            type=InvalidationType.DYNAMIC_STRUCTURAL,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"close {ctx.candle_close} < AIL {ctx.ail.price} (dynamic structural fail)",
        )
    return None


def origin_invalidation(ctx: InvalidationContext) -> InvalidationEvent | None:
    """Young trend + Main High breach kills (more sensitive than standard)."""
    if not ctx.is_young_trend:
        return None
    return absolute_kill_switch(ctx) and InvalidationEvent(  # propagate when triggered
        type=InvalidationType.ORIGIN,
        timestamp_ms=ctx.candle_ts_ms,
        triggered_at_price=ctx.candle_close,
        rationale="young trend + Main High breach (origin invalidation)",
    )


def macro_cycle_reset(ctx: InvalidationContext) -> InvalidationEvent | None:
    """2 major structure breaks consecutively without entry -> full reset."""
    if ctx.consecutive_break_count_no_entry >= 2:
        return InvalidationEvent(
            type=InvalidationType.MACRO_CYCLE_RESET,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"{ctx.consecutive_break_count_no_entry} structure breaks without entry",
        )
    return None


def ipa_halt(ctx: InvalidationContext) -> InvalidationEvent | None:
    """2 consecutive unfilled TLQs -> suspend (NOT kill)."""
    if ctx.efficiency_state is EfficiencyState.IPA_FROZEN:
        return InvalidationEvent(
            type=InvalidationType.IPA_HALT,
            timestamp_ms=ctx.candle_ts_ms,
            triggered_at_price=ctx.candle_close,
            rationale=f"IPA frozen ({ctx.consecutive_misses} consecutive misses)",
        )
    return None


ALL_PREDICATES = (
    absolute_kill_switch,
    origin_invalidation,
    standard_invalidation,
    dynamic_structural_invalidation,
    macro_cycle_reset,
    ipa_halt,
)


def first_invalidation(ctx: InvalidationContext) -> InvalidationEvent | None:
    """Run predicates in priority order; return the first one that fires."""
    for fn in ALL_PREDICATES:
        ev = fn(ctx)
        if ev is not None:
            return ev
    return None
