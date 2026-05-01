"""Higher-timeframe trend filter (§1.5.6).

Maintains HTF trend direction via a configurable-period EMA on aggregated
HTF candles. Signals against the HTF trend get a confidence multiplier of
0.5; with-trend get 1.0.

Inputs are arbitrary-window candles; we aggregate to the HTF window by
floor(ts / htf_window_ms). Default htf_window_ms = 3_600_000 (1 hour),
matching the legacy 5m → 1H aggregation.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import Enum

from polybot.truth.models import Candle


_HOUR_MS = 3_600_000


class HtfTrend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


def _aggregate_to_window(candles: Sequence[Candle], window_ms: int) -> list[Candle]:
    if not candles:
        return []
    out: list[Candle] = []
    bucket: list[Candle] = []
    cur_bin = candles[0].ts_ms // window_ms
    for c in candles:
        b = c.ts_ms // window_ms
        if b != cur_bin and bucket:
            out.append(_collapse(bucket, cur_bin, window_ms))
            bucket = []
            cur_bin = b
        bucket.append(c)
    if bucket:
        out.append(_collapse(bucket, cur_bin, window_ms))
    return out


# Legacy alias — keep the old name working for any external callers
def _aggregate_to_hourly(candles: Sequence[Candle]) -> list[Candle]:
    return _aggregate_to_window(candles, _HOUR_MS)


def _collapse(bucket: list[Candle], bin_idx: int, window_ms: int = _HOUR_MS) -> Candle:
    return Candle(
        ts_ms=bin_idx * window_ms,
        open=bucket[0].open,
        high=max(c.high for c in bucket),
        low=min(c.low for c in bucket),
        close=bucket[-1].close,
        volume=sum((c.volume for c in bucket), Decimal(0)),
        n_ticks=sum(c.n_ticks for c in bucket),
    )


def ema(closes: Sequence[Decimal], period: int) -> list[Decimal]:
    """Standard EMA. Output has same length as input; first `period-1` entries
    are simple-moving-average seeded for stability."""
    if not closes:
        return []
    if period <= 0:
        raise ValueError("period must be positive")
    k = Decimal(2) / (Decimal(period) + Decimal(1))
    out: list[Decimal] = []
    for i, p in enumerate(closes):
        if i == 0:
            out.append(p)
        else:
            prev = out[-1]
            out.append(prev + k * (p - prev))
    return out


class HtfFilter:
    """Higher-timeframe trend filter.

    Args:
        period: EMA period over HTF-aggregated closes (default 50).
        htf_window_ms: HTF aggregation window in milliseconds (default 1H).
    """

    def __init__(self, *, period: int = 50, htf_window_ms: int = _HOUR_MS) -> None:
        self._period = period
        self._htf_window_ms = htf_window_ms

    def trend(self, candles: Sequence[Candle]) -> HtfTrend:
        htf = _aggregate_to_window(candles, self._htf_window_ms)
        if len(htf) < self._period:
            return HtfTrend.NEUTRAL
        ema_vals = ema([c.close for c in htf], self._period)
        # Trend is UP if last close > EMA AND EMA is rising over last 3 HTF bars
        last_close = htf[-1].close
        last_ema = ema_vals[-1]
        prev_ema = ema_vals[-3] if len(ema_vals) >= 3 else ema_vals[-1]
        if last_close > last_ema and last_ema >= prev_ema:
            return HtfTrend.UP
        if last_close < last_ema and last_ema <= prev_ema:
            return HtfTrend.DOWN
        return HtfTrend.NEUTRAL

    def alignment_multiplier(
        self,
        candles: Sequence[Candle],
        signal_direction: str,
        *,
        against_multiplier: Decimal = Decimal("0.5"),
    ) -> Decimal:
        """Return 1.0 if signal direction agrees with HTF trend, else `against_multiplier`."""
        htf = self.trend(candles)
        if htf is HtfTrend.NEUTRAL:
            return Decimal("1.0")
        if signal_direction.upper() == "DOWN" and htf is HtfTrend.DOWN:
            return Decimal("1.0")
        if signal_direction.upper() == "UP" and htf is HtfTrend.UP:
            return Decimal("1.0")
        return against_multiplier
