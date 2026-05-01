"""§1.5.1 continuation filter tests."""

from __future__ import annotations

from decimal import Decimal

from polybot.signal.continuation_filter import (
    ContinuationFilterParams,
    evaluate,
)
from polybot.signal.models import (
    SignalDirection,
    SnDZone,
    SndPattern,
)
from polybot.truth.models import BtcSource, BtcTick, Candle


def C(o: float, h: float, l: float, c: float, ts: int = 0) -> Candle:
    return Candle(
        ts_ms=ts, open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)),
        volume=Decimal(1), n_ticks=1,
    )


def Z(top: float, bot: float, direction: str = "SUPPLY") -> SnDZone:
    return SnDZone(
        top=Decimal(str(top)), bottom=Decimal(str(bot)),
        structure_type=SndPattern.DOJI, direction=direction,
        formed_at_ms=0, source_candle_indices=(0,),
        formation_volume_ratio=Decimal(1), pattern_confidence=Decimal("0.7"),
        half_life_min=Decimal(30), max_age_min=Decimal(120),
    )


def ticks_descending(n: int = 30, start: float = 100050, step: float = 0.5) -> list[BtcTick]:
    return [
        BtcTick(source=BtcSource.BINANCE, ts_ms=i, price=Decimal(str(start - i * step)),
                volume=Decimal(0))
        for i in range(n)
    ]


def ticks_ascending(n: int = 30, start: float = 100000, step: float = 0.5) -> list[BtcTick]:
    return [
        BtcTick(source=BtcSource.BINANCE, ts_ms=i, price=Decimal(str(start + i * step)),
                volume=Decimal(0))
        for i in range(n)
    ]


def test_passes_when_all_three_predicates_pass() -> None:
    # Supply zone at [99500, 99800]; bearish candle that wicked into zone and closed below
    candle = C(o=99700, h=99750, l=99200, c=99300)
    zone = Z(99800, 99500, "SUPPLY")
    params = ContinuationFilterParams(min_zone_penetration_bps=2)
    res = evaluate(
        candle=candle, zone=zone, recent_ticks=ticks_descending(),
        signal_direction=SignalDirection.DOWN, params=params,
    )
    assert res.passed
    assert res.close_open_agreement
    assert res.penetration_bps_ok
    assert res.tick_slope_agreement_ok


def test_fails_when_close_open_disagrees() -> None:
    # Bullish candle but DOWN signal — should fail
    candle = C(o=99300, h=99750, l=99200, c=99700)
    zone = Z(99800, 99500, "SUPPLY")
    params = ContinuationFilterParams(min_zone_penetration_bps=2)
    res = evaluate(
        candle=candle, zone=zone, recent_ticks=ticks_descending(),
        signal_direction=SignalDirection.DOWN, params=params,
    )
    assert not res.passed
    assert not res.close_open_agreement


def test_fails_when_penetration_too_shallow() -> None:
    # Wick barely touches zone bottom
    candle = C(o=99700, h=99500.5, l=99200, c=99300)
    zone = Z(99800, 99500, "SUPPLY")
    # Penetration ~ (99500.5 - 99500) / 99500 * 10000 = ~0.05 bps; require >= 2
    params = ContinuationFilterParams(min_zone_penetration_bps=2)
    res = evaluate(
        candle=candle, zone=zone, recent_ticks=ticks_descending(),
        signal_direction=SignalDirection.DOWN, params=params,
    )
    assert not res.passed
    assert not res.penetration_bps_ok


def test_fails_when_tick_slope_disagrees() -> None:
    candle = C(o=99700, h=99750, l=99200, c=99300)
    zone = Z(99800, 99500, "SUPPLY")
    params = ContinuationFilterParams(min_zone_penetration_bps=2)
    res = evaluate(
        candle=candle, zone=zone, recent_ticks=ticks_ascending(),
        signal_direction=SignalDirection.DOWN, params=params,
    )
    assert not res.passed
    assert not res.tick_slope_agreement_ok


def test_passes_demand_mirror() -> None:
    candle = C(o=99300, h=99800, l=99250, c=99700)
    zone = Z(99500, 99200, "DEMAND")
    params = ContinuationFilterParams(min_zone_penetration_bps=2)
    res = evaluate(
        candle=candle, zone=zone, recent_ticks=ticks_ascending(),
        signal_direction=SignalDirection.UP, params=params,
    )
    assert res.passed
