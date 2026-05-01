"""Tests for signal/pivots.py — tentative vs confirmed."""

from __future__ import annotations

from decimal import Decimal

from polybot.signal.models import PivotType
from polybot.signal.pivots import (
    PivotTracker,
    find_all_pivots,
    find_swing_highs,
    find_swing_lows,
    is_swing_high,
    is_swing_low,
)
from polybot.truth.models import Candle


def C(i: int, h: float, l: float, o: float | None = None, c: float | None = None) -> Candle:
    return Candle(
        ts_ms=i * 300_000,
        open=Decimal(str(o if o is not None else (h + l) / 2)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c if c is not None else (h + l) / 2)),
        volume=Decimal(1),
        n_ticks=1,
    )


def test_swing_high_clean_5_candle_pattern() -> None:
    # ascend, peak, descend
    candles = [C(0, 100, 90), C(1, 105, 95), C(2, 110, 100), C(3, 105, 95), C(4, 100, 90)]
    assert is_swing_high(candles, 2, lookback=2)
    assert not is_swing_high(candles, 1, lookback=2)
    assert not is_swing_high(candles, 0, lookback=2)


def test_swing_low_clean() -> None:
    candles = [C(0, 100, 95), C(1, 95, 90), C(2, 90, 85), C(3, 95, 90), C(4, 100, 95)]
    assert is_swing_low(candles, 2, lookback=2)
    assert not is_swing_low(candles, 1, lookback=2)


def test_no_pivot_on_equal_highs() -> None:
    # Pivot requires strictly greater highs, not equal
    candles = [C(0, 100, 95), C(1, 110, 100), C(2, 110, 100), C(3, 105, 95), C(4, 100, 90)]
    assert not is_swing_high(candles, 1, lookback=2)
    assert not is_swing_high(candles, 2, lookback=2)


def test_find_all_pivots_basic() -> None:
    candles = [
        C(0, 100, 90), C(1, 105, 95),
        C(2, 110, 100),                     # high
        C(3, 105, 95), C(4, 100, 85),       # low at 4
        C(5, 95, 80),                       # low at 5
        C(6, 105, 90), C(7, 115, 100),      # high at 7? no — needs lookback 2 each side
        C(8, 110, 95), C(9, 105, 90),
    ]
    pivots = find_all_pivots(candles, lookback=2)
    # At least the index-2 swing high and index-5 swing low are confirmed
    assert any(p.index == 2 and p.type is PivotType.HIGH for p in pivots)
    assert any(p.index == 5 and p.type is PivotType.LOW for p in pivots)
    # All returned pivots must be is_confirmed=True
    assert all(p.is_confirmed for p in pivots)


def test_pivot_tracker_confirms_after_lookback() -> None:
    tracker = PivotTracker(lookback=2)
    # Feed an ascend-peak-descend sequence
    seq = [(0, 100, 90), (1, 105, 95), (2, 110, 100), (3, 105, 95), (4, 100, 90)]
    confirmations: list[int] = []
    for i, h, l in seq:
        new = tracker.on_candle_close(C(i, h, l))
        for p in new:
            confirmations.append(p.index)
    # After candle 4 closes, the swing high at index 2 should be confirmed
    assert 2 in confirmations
    assert any(p.index == 2 and p.type is PivotType.HIGH for p in tracker.confirmed)


def test_pivot_tracker_tentative_until_confirmed() -> None:
    tracker = PivotTracker(lookback=2)
    # First 3 candles: index 2 is a candidate (left side OK) but not confirmed
    for i, h, l in [(0, 100, 90), (1, 105, 95), (2, 110, 100)]:
        tracker.on_candle_close(C(i, h, l))
    # No confirmed pivots yet — needs 2 more bars after candidate
    assert tracker.confirmed == []
    # Tentative MUST include the index-2 high candidate (left-side criterion satisfied)
    tent = tracker.tentative
    assert any(p.index == 2 and p.type is PivotType.HIGH and not p.is_confirmed for p in tent)


def test_pivot_tracker_confirmation_age_ms() -> None:
    tracker = PivotTracker(lookback=2)
    seq = [(0, 100, 90), (1, 105, 95), (2, 110, 100), (3, 105, 95), (4, 100, 90)]
    confirmed_pivot = None
    for i, h, l in seq:
        new = tracker.on_candle_close(C(i, h, l))
        if new:
            confirmed_pivot = new[0]
    assert confirmed_pivot is not None
    age = tracker.confirmation_age_ms(confirmed_pivot)
    # Confirmation requires +2 candles, each 5 minutes apart -> 10 minutes
    assert age == 2 * 300_000
