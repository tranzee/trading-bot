"""Tests for signal/liquidity.py — 4-tier hierarchy promotion rules."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polybot.signal.liquidity import LiquidityHierarchy
from polybot.signal.models import Pivot, PivotType, Trend


def P(idx: int, price: float, t: PivotType) -> Pivot:
    return Pivot(
        index=idx,
        timestamp_ms=idx * 300_000,
        price=Decimal(str(price)),
        type=t,
        is_confirmed=True,
    )


def test_seed_main_for_downtrend_requires_high_pivot() -> None:
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    assert h.current_main_high() is not None
    assert h.current_main_high().price == Decimal("100000")
    with pytest.raises(ValueError):
        LiquidityHierarchy(Trend.DOWN).seed_main(P(0, 100000, PivotType.LOW))


def test_first_tlq_break_promotes_slq_in_downtrend() -> None:
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    # Pivot sequence: low (initial TLQ), high (lower-high candidate), lower-low (breaks TLQ)
    h.update(P(1, 99000, PivotType.LOW))    # initial TLQ
    h.update(P(2, 99500, PivotType.HIGH))   # lower-high candidate -> latest_against_pivot
    h.update(P(3, 98800, PivotType.LOW))    # breaks 99000 -> SLQ promoted, ILQ set
    assert h.current_slq() is not None
    assert h.current_slq().price == Decimal("99500")
    assert h.current_ilq() is not None
    assert h.current_ilq().price == Decimal("99500")
    assert h.current_tlq().price == Decimal("98800")
    assert h.confirmed_tlq_breaks_since_slq == 1


def test_subsequent_tlq_breaks_update_ilq_not_slq() -> None:
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    h.update(P(1, 99000, PivotType.LOW))
    h.update(P(2, 99500, PivotType.HIGH))
    h.update(P(3, 98800, PivotType.LOW))    # SLQ=99500
    h.update(P(4, 99100, PivotType.HIGH))   # new lower-high
    h.update(P(5, 98500, PivotType.LOW))    # breaks 98800 -> ILQ becomes 99100
    assert h.current_slq().price == Decimal("99500")  # static
    assert h.current_ilq().price == Decimal("99100")
    assert h.current_tlq().price == Decimal("98500")
    assert h.confirmed_tlq_breaks_since_slq == 2


def test_main_high_does_not_update_in_downtrend() -> None:
    """Per blueprint: MAIN HIGH is static once set in a downtrend."""
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    # Even if a higher high arrives (which would be macro reversal), the
    # hierarchy itself does NOT update MAIN HIGH — invalidation.py handles
    # that scenario.
    h.update(P(1, 101000, PivotType.HIGH))
    assert h.current_main_high().price == Decimal("100000")


def test_uptrend_mirror() -> None:
    h = LiquidityHierarchy(Trend.UP)
    h.seed_main(P(0, 90000, PivotType.LOW))
    h.update(P(1, 91000, PivotType.HIGH))     # initial TLQ
    h.update(P(2, 90500, PivotType.LOW))      # higher-low candidate
    h.update(P(3, 92000, PivotType.HIGH))     # breaks 91000 -> SLQ promoted
    assert h.current_slq().price == Decimal("90500")
    assert h.current_ilq().price == Decimal("90500")
    assert h.current_tlq().price == Decimal("92000")
    assert h.confirmed_tlq_breaks_since_slq == 1


def test_rejects_unconfirmed_pivot() -> None:
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    tentative = Pivot(
        index=1, timestamp_ms=300000, price=Decimal("99000"),
        type=PivotType.LOW, is_confirmed=False,
    )
    with pytest.raises(ValueError):
        h.update(tentative)


def test_low_that_doesnt_break_tlq_doesnt_count() -> None:
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    h.update(P(1, 99000, PivotType.LOW))    # initial TLQ
    h.update(P(2, 99500, PivotType.HIGH))   # lower-high
    h.update(P(3, 99100, PivotType.LOW))    # NOT a break (99100 > 99000)
    # No SLQ should be promoted, no break recorded
    assert h.current_slq() is None
    assert h.confirmed_tlq_breaks_since_slq == 0
    # TLQ updates to the most recent low regardless
    assert h.current_tlq().price == Decimal("99100")


def test_snapshot_keys() -> None:
    h = LiquidityHierarchy(Trend.DOWN)
    h.seed_main(P(0, 100000, PivotType.HIGH))
    snap = h.snapshot()
    assert set(snap.keys()) == {
        "direction", "main_high", "main_low", "slq", "tlq", "ilq", "tlq_breaks_since_slq",
    }
