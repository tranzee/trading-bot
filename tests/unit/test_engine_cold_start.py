"""Engine cold-start (§6.2.9) — deterministic and idempotent."""

from __future__ import annotations

from decimal import Decimal

from polybot.signal.engine import PriceActionEngine, StrategyParams
from polybot.signal.models import Trend
from polybot.truth.models import Candle


def make_downtrend_candles(n: int = 50, start: float = 100000) -> list[Candle]:
    """Generate a clean downtrend with detectable swing pivots (lookback=2).

    Pattern uses a 6-candle cycle: descend 3, ascend 2, then form an
    isolated swing high/low. Each "trough" candle has a strictly lower
    low than its 2 neighbors on each side, giving find_swing_lows a
    valid pivot.
    """
    out: list[Candle] = []
    # Hand-shape an OHLC sequence with explicit local extrema.
    # We'll construct in pure descending steps with clear V-shape troughs.
    rows: list[tuple[float, float, float, float]] = []
    price = start
    cycle_step = 200.0
    for cycle in range(n // 6):
        anchor = price
        # 6-bar cycle:
        # bar 0: gentle descent
        rows.append((anchor, anchor + 10, anchor - 30, anchor - 30))
        # bar 1: ascent shallow
        rows.append((anchor - 30, anchor - 10, anchor - 35, anchor - 15))
        # bar 2: TROUGH — sharp drop and recover
        trough_low = anchor - cycle_step - 20
        rows.append((anchor - 15, anchor - 10, trough_low, anchor - 80))
        # bar 3: PEAK — sharp ascent and pull back
        peak_high = anchor - 50
        rows.append((anchor - 80, peak_high, anchor - 90, anchor - 90))
        # bar 4: descent
        rows.append((anchor - 90, anchor - 80, anchor - cycle_step, anchor - cycle_step))
        # bar 5: continuation
        next_anchor = anchor - cycle_step - 20
        rows.append((anchor - cycle_step, anchor - cycle_step + 10,
                     next_anchor - 5, next_anchor))
        price = next_anchor
    # If we still need more bars, pad with simple descents
    while len(rows) < n:
        anchor = price
        rows.append((anchor, anchor + 5, anchor - 25, anchor - 25))
        price = anchor - 25

    for i, (o, h, lo, c) in enumerate(rows[:n]):
        out.append(Candle(
            ts_ms=i * 300_000 + 1_700_000_000_000,
            open=Decimal(str(o)), high=Decimal(str(h)),
            low=Decimal(str(lo)), close=Decimal(str(c)),
            volume=Decimal(1), n_ticks=10,
        ))
    return out


def test_bootstrap_is_idempotent() -> None:
    candles = make_downtrend_candles(n=80)
    e1 = PriceActionEngine()
    e1.bootstrap_from_history(candles)
    e2 = PriceActionEngine()
    e2.bootstrap_from_history(candles)
    assert e1.state.trend == e2.state.trend
    assert (e1.state.main_high.price if e1.state.main_high else None) == (
        e2.state.main_high.price if e2.state.main_high else None
    )
    assert (e1.state.slq.price if e1.state.slq else None) == (
        e2.state.slq.price if e2.state.slq else None
    )


def test_bootstrap_too_few_candles_pending() -> None:
    e = PriceActionEngine(StrategyParams(pivot_lookback=2, cold_start_lookback=200))
    candles = make_downtrend_candles(n=3)
    e.bootstrap_from_history(candles)
    assert e.state.trend is Trend.NEW_TREND_PENDING
    assert not e.is_ready


def test_bootstrap_downtrend_detected() -> None:
    candles = make_downtrend_candles(n=80, start=100000)
    e = PriceActionEngine()
    e.bootstrap_from_history(candles)
    assert e.state.trend is Trend.DOWN
    assert e.state.main_high is not None
    # Main High should be from an early candle (the highest absolute high)
    assert e.state.main_high.price >= Decimal("99000")
    # Confirmed pivots populated
    assert len(e.state.confirmed_pivots) > 0
    assert e.is_ready
