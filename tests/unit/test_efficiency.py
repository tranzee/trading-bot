"""EPA/IPA state machine tests."""

from __future__ import annotations

from decimal import Decimal

from polybot.signal.efficiency import EfficiencyTracker
from polybot.signal.models import (
    EfficiencyState,
    EfficiencyStatus,
    LiquidityNode,
    NodeType,
    PivotType,
)


def TLQ(price: float, ts: int) -> LiquidityNode:
    return LiquidityNode(
        price=Decimal(str(price)),
        timestamp_ms=ts,
        node_type=NodeType.TLQ,
        is_static=False,
        direction=PivotType.LOW,
        formed_at_pivot_index=ts // 300_000,
        efficiency_status=EfficiencyStatus.PENDING,
    )


def test_first_break_enters_epa_c2() -> None:
    e = EfficiencyTracker()
    e.on_tlq_break(TLQ(99000, 1000), tlq_was_touched_during_pullback=False)
    assert e.state is EfficiencyState.EPA_C2


def test_break_with_touch_keeps_efficiency() -> None:
    e = EfficiencyTracker()
    e.on_tlq_break(TLQ(99000, 1000), tlq_was_touched_during_pullback=False)
    e.on_tlq_break(TLQ(98800, 2000), tlq_was_touched_during_pullback=True)
    assert e.state is EfficiencyState.EFFICIENT
    assert e.consecutive_misses == 0


def test_two_consecutive_misses_freeze() -> None:
    e = EfficiencyTracker()
    e.on_tlq_break(TLQ(99000, 1000), tlq_was_touched_during_pullback=False)  # C2
    e.on_tlq_break(TLQ(98800, 2000), tlq_was_touched_during_pullback=False)  # miss 1
    e.on_tlq_break(TLQ(98500, 3000), tlq_was_touched_during_pullback=False)  # miss 2 -> FROZEN
    assert e.state is EfficiencyState.IPA_FROZEN
    assert not e.is_signal_allowed()


def test_pullback_touch_unfreezes() -> None:
    e = EfficiencyTracker()
    tlq1 = TLQ(99000, 1000)
    tlq2 = TLQ(98800, 2000)
    tlq3 = TLQ(98500, 3000)
    e.on_tlq_break(tlq1, tlq_was_touched_during_pullback=False)
    e.on_tlq_break(tlq2, tlq_was_touched_during_pullback=False)
    e.on_tlq_break(tlq3, tlq_was_touched_during_pullback=False)
    assert e.state is EfficiencyState.IPA_FROZEN
    cleared = e.on_pullback_touch(Decimal("98800"))
    assert cleared
    assert e.state is EfficiencyState.EFFICIENT
    assert e.is_signal_allowed()


def test_unfilled_tlqs_accumulate() -> None:
    e = EfficiencyTracker()
    e.on_tlq_break(TLQ(99000, 1000), tlq_was_touched_during_pullback=False)
    e.on_tlq_break(TLQ(98800, 2000), tlq_was_touched_during_pullback=False)
    assert len(e.unfilled_tlqs) == 2
