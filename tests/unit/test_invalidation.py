"""Invalidation kill-switch predicate tests."""

from __future__ import annotations

from decimal import Decimal

from polybot.signal.invalidation import (
    InvalidationContext,
    absolute_kill_switch,
    first_invalidation,
    ipa_halt,
    standard_invalidation,
)
from polybot.signal.models import (
    EfficiencyState,
    LiquidityNode,
    NodeType,
    PivotType,
    Trend,
)


def NODE(price: float, t: PivotType = PivotType.HIGH) -> LiquidityNode:
    return LiquidityNode(
        price=Decimal(str(price)),
        timestamp_ms=0,
        node_type=NodeType.SLQ if t is PivotType.HIGH else NodeType.TLQ,
        is_static=True,
        direction=t,
        formed_at_pivot_index=0,
    )


def base_ctx(**kw) -> InvalidationContext:
    defaults = dict(
        direction=Trend.DOWN,
        candle_close=Decimal("99000"),
        candle_high=Decimal("99100"),
        candle_low=Decimal("98900"),
        candle_ts_ms=1000,
        main_high=NODE(100000),
        main_low=NODE(98000, PivotType.LOW),
        slq=NODE(99500),
        ail=None,
        is_young_trend=False,
        efficiency_state=EfficiencyState.EFFICIENT,
        consecutive_misses=0,
        consecutive_break_count_no_entry=0,
        basis_bps=1,
    )
    defaults.update(kw)
    return InvalidationContext(**defaults)


def test_standard_fires_on_close_above_slq_in_downtrend() -> None:
    ctx = base_ctx(candle_close=Decimal("99600"))
    ev = standard_invalidation(ctx)
    assert ev is not None
    assert "SLQ" in ev.rationale


def test_standard_does_not_fire_at_slq() -> None:
    ctx = base_ctx(candle_close=Decimal("99500"))   # exactly at SLQ
    ev = standard_invalidation(ctx)
    assert ev is None


def test_absolute_kill_switch_main_high_breach() -> None:
    ctx = base_ctx(candle_close=Decimal("100050"))
    ev = absolute_kill_switch(ctx)
    assert ev is not None


def test_ipa_halt_when_frozen() -> None:
    ctx = base_ctx(efficiency_state=EfficiencyState.IPA_FROZEN, consecutive_misses=2)
    ev = ipa_halt(ctx)
    assert ev is not None


def test_first_invalidation_priority() -> None:
    """Main High breach takes priority over SLQ breach."""
    ctx = base_ctx(candle_close=Decimal("100050"))
    ev = first_invalidation(ctx)
    assert ev is not None
    # absolute_kill_switch is first in the priority list
    assert "Main High" in ev.rationale or "macro" in ev.rationale.lower()
