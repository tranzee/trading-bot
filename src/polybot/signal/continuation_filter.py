"""Continuation filter (mitigation §1.5.1).

Pure function called from engine.on_candle_close() AFTER Alert-3 candidate
criteria pass and BEFORE emitting Signal. Three predicates:

    1. Close-vs-open delta sign agrees with signal direction.
    2. Zone penetration in bps >= min_zone_penetration_bps.
    3. Of the last `tick_slope_window_n` ticks, the fraction whose 1-tick
       delta sign agrees with signal_direction is >= min_sign_agreement.

ALL three must pass or Alert-3 is downgraded to log-only and no Signal is
emitted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from polybot.signal import math as M
from polybot.signal.models import (
    ContinuationCheckResult,
    SignalDirection,
    SnDZone,
)
from polybot.truth.models import BtcTick, Candle


@dataclass(frozen=True)
class ContinuationFilterParams:
    require_close_open_agreement: bool = True
    min_zone_penetration_bps: int = 2
    tick_slope_window_n: int = 30
    tick_slope_min_sign_agreement: Decimal = Decimal("0.6")


def evaluate(
    *,
    candle: Candle,
    zone: SnDZone,
    recent_ticks: Sequence[BtcTick],
    signal_direction: SignalDirection,
    params: ContinuationFilterParams,
) -> ContinuationCheckResult:
    # 1. Close-vs-open agreement
    if signal_direction is SignalDirection.DOWN:
        co_agree = candle.close < candle.open
    else:
        co_agree = candle.close > candle.open

    # 2. Zone penetration in bps
    if zone.direction == "SUPPLY":
        # signal=DOWN: rejected from supply; penetration depth measures how
        # far INTO the zone the wick reached. Higher penetration = deeper
        # rejection (mitigation §1.5.1 paradox).
        penetration_bps = M.rejection_depth_bps(
            zone_top=zone.top, zone_bottom=zone.bottom,
            candle_high=candle.high, candle_low=candle.low,
            direction="SUPPLY",
        )
    else:
        penetration_bps = M.rejection_depth_bps(
            zone_top=zone.top, zone_bottom=zone.bottom,
            candle_high=candle.high, candle_low=candle.low,
            direction="DEMAND",
        )
    pen_ok = penetration_bps >= Decimal(params.min_zone_penetration_bps)

    # 3. Tick-slope agreement
    ticks = list(recent_ticks)[-params.tick_slope_window_n :]
    if len(ticks) < 2:
        slope_agree = Decimal(0)
    else:
        agree_count = 0
        for prev, cur in zip(ticks[:-1], ticks[1:], strict=False):
            delta = cur.price - prev.price
            if signal_direction is SignalDirection.DOWN:
                if delta < 0:
                    agree_count += 1
            else:
                if delta > 0:
                    agree_count += 1
        n_pairs = len(ticks) - 1
        slope_agree = Decimal(agree_count) / Decimal(n_pairs) if n_pairs > 0 else Decimal(0)
    slope_ok = slope_agree >= params.tick_slope_min_sign_agreement

    co_check = (not params.require_close_open_agreement) or co_agree
    return ContinuationCheckResult(
        passed=bool(co_check and pen_ok and slope_ok),
        close_open_agreement=co_agree,
        penetration_bps_ok=pen_ok,
        tick_slope_agreement_ok=slope_ok,
        penetration_bps=penetration_bps,
        tick_slope_agreement_fraction=slope_agree,
    )
