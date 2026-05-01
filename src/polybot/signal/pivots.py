"""Pivot detection — swing highs and lows.

§6.2.1 — color-independent (only OHLC values matter; close-vs-open is used
downstream as bias, not as structure determinant).

Mitigation §1.5.2 — the engine maintains TWO lists:
    - tentative_pivots: candidates formed at candle N awaiting confirmation
      from N+lookback subsequent candles.
    - confirmed_pivots: tentative pivots whose confirming candles have closed.

ONLY confirmed_pivots feed downstream (liquidity hierarchy, efficiency state).
Tentative pivots are exposed for charting and Alert-2 only.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from polybot.signal.models import Pivot, PivotType
from polybot.truth.models import Candle


def is_swing_high(candles: Sequence[Candle], i: int, lookback: int) -> bool:
    """A swing high at index i requires:
        high[i] > max(high[i-lookback : i])  AND
        high[i] > max(high[i+1 : i+1+lookback])

    Edge handling: returns False if i is too close to either end.
    """
    if i < lookback or i + lookback >= len(candles):
        return False
    pivot_high = candles[i].high
    left = max(c.high for c in candles[i - lookback : i])
    right = max(c.high for c in candles[i + 1 : i + 1 + lookback])
    return pivot_high > left and pivot_high > right


def is_swing_low(candles: Sequence[Candle], i: int, lookback: int) -> bool:
    if i < lookback or i + lookback >= len(candles):
        return False
    pivot_low = candles[i].low
    left = min(c.low for c in candles[i - lookback : i])
    right = min(c.low for c in candles[i + 1 : i + 1 + lookback])
    return pivot_low < left and pivot_low < right


def find_swing_highs(candles: Sequence[Candle], lookback: int = 2) -> list[Pivot]:
    """Confirmed swing highs over the entire candle sequence."""
    out: list[Pivot] = []
    for i in range(lookback, len(candles) - lookback):
        if is_swing_high(candles, i, lookback):
            out.append(
                Pivot(
                    index=i,
                    timestamp_ms=candles[i].ts_ms,
                    price=candles[i].high,
                    type=PivotType.HIGH,
                    is_confirmed=True,
                )
            )
    return out


def find_swing_lows(candles: Sequence[Candle], lookback: int = 2) -> list[Pivot]:
    out: list[Pivot] = []
    for i in range(lookback, len(candles) - lookback):
        if is_swing_low(candles, i, lookback):
            out.append(
                Pivot(
                    index=i,
                    timestamp_ms=candles[i].ts_ms,
                    price=candles[i].low,
                    type=PivotType.LOW,
                    is_confirmed=True,
                )
            )
    return out


def find_all_pivots(candles: Sequence[Candle], lookback: int = 2) -> list[Pivot]:
    """All confirmed pivots, sorted by candle index then by type."""
    pivots = find_swing_highs(candles, lookback) + find_swing_lows(candles, lookback)
    pivots.sort(key=lambda p: (p.index, 0 if p.type is PivotType.HIGH else 1))
    return pivots


class PivotTracker:
    """Stateful tracker that ingests candles one by one and maintains two lists.

    Use during live operation. For one-shot batch scans (e.g. cold-start),
    `find_all_pivots` is simpler.
    """

    def __init__(self, lookback: int = 2) -> None:
        self._lookback = lookback
        self._candles: list[Candle] = []
        self._confirmed: list[Pivot] = []

    @property
    def confirmed(self) -> list[Pivot]:
        return list(self._confirmed)

    @property
    def tentative(self) -> list[Pivot]:
        """Pivots formed but not yet confirmed (within `lookback` of the live tip)."""
        out: list[Pivot] = []
        n = len(self._candles)
        # A candidate exists at index i where lookback <= i and the right-side
        # comparison can't be done yet because we have fewer than lookback bars after.
        for i in range(max(0, n - self._lookback), n):
            if i < self._lookback:
                continue
            # Compare against left only — right is unknown yet.
            if self._candles[i].high > max(c.high for c in self._candles[i - self._lookback : i]):
                out.append(
                    Pivot(
                        index=i,
                        timestamp_ms=self._candles[i].ts_ms,
                        price=self._candles[i].high,
                        type=PivotType.HIGH,
                        is_confirmed=False,
                    )
                )
            if self._candles[i].low < min(c.low for c in self._candles[i - self._lookback : i]):
                out.append(
                    Pivot(
                        index=i,
                        timestamp_ms=self._candles[i].ts_ms,
                        price=self._candles[i].low,
                        type=PivotType.LOW,
                        is_confirmed=False,
                    )
                )
        return out

    def on_candle_close(self, candle: Candle) -> list[Pivot]:
        """Append a candle. Returns any pivots NEWLY confirmed as a result."""
        self._candles.append(candle)
        new_confirmed: list[Pivot] = []
        # When candle at index n-1 closes, a pivot at index (n-1-lookback) can
        # be confirmed if both sides are consistent with the swing definition.
        candidate_idx = len(self._candles) - 1 - self._lookback
        if candidate_idx >= self._lookback:
            if is_swing_high(self._candles, candidate_idx, self._lookback):
                new_confirmed.append(
                    Pivot(
                        index=candidate_idx,
                        timestamp_ms=self._candles[candidate_idx].ts_ms,
                        price=self._candles[candidate_idx].high,
                        type=PivotType.HIGH,
                        is_confirmed=True,
                    )
                )
            if is_swing_low(self._candles, candidate_idx, self._lookback):
                new_confirmed.append(
                    Pivot(
                        index=candidate_idx,
                        timestamp_ms=self._candles[candidate_idx].ts_ms,
                        price=self._candles[candidate_idx].low,
                        type=PivotType.LOW,
                        is_confirmed=True,
                    )
                )
        self._confirmed.extend(new_confirmed)
        return new_confirmed

    def confirmation_age_ms(self, pivot: Pivot) -> int:
        """Wall-clock ms between the candle that formed the pivot and the
        candle that confirmed it. Used in signal logging per §1.5.2."""
        if not pivot.is_confirmed:
            return 0
        if pivot.index + self._lookback < len(self._candles):
            return self._candles[pivot.index + self._lookback].ts_ms - pivot.timestamp_ms
        return 0


def _candle_factory(
    ts_ms: int, o: Decimal, h: Decimal, low: Decimal, c: Decimal, v: Decimal = Decimal(1)
) -> Candle:
    """Convenience factory used by tests."""
    return Candle(ts_ms=ts_ms, open=o, high=h, low=low, close=c, volume=v, n_ticks=1)
