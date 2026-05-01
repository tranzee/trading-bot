"""Pure mathematical predicates for the price action engine.

§6.2.11 — BTC-specific definitions. Every PA module imports from here; no
inline math in the engine. All functions are pure and deterministic;
property-tested in tests/property/test_math_invariants.py.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from polybot.signal.models import Trend


def min_breach_distance(level: Decimal, basis_bps: int) -> Decimal:
    """Required absolute distance for a breach to count, in price units.

    `min_breach = level * (basis_bps / 10_000)`. Default 1 bp ($10 at $100k BTC).
    """
    if level <= 0:
        raise ValueError(f"level must be positive; got {level}")
    if basis_bps < 0:
        raise ValueError(f"basis_bps must be non-negative; got {basis_bps}")
    return level * Decimal(basis_bps) / Decimal(10_000)


def touched(level: Decimal, candle_low: Decimal, candle_high: Decimal) -> bool:
    """A candle 'touched' a level iff the level lies within [low, high]."""
    return candle_low <= level <= candle_high


def swept(
    level: Decimal,
    side: Literal["above", "below"],
    price: Decimal,
    *,
    basis_bps: int = 1,
) -> bool:
    """Sweep test: did `price` traverse `level` by at least `basis_bps`?

    side='above' means swept by going UP through the level (e.g., a swing low
    being swept by price moving above it after testing it from below — used
    in uptrend logic). side='below' is the downtrend counterpart.
    """
    distance = min_breach_distance(level, basis_bps)
    if side == "above":
        return price > level + distance
    return price < level - distance


def broken(
    level: Decimal,
    direction: Literal["up", "down"],
    candle_close: Decimal,
    *,
    basis_bps: int = 1,
) -> bool:
    """Structural break: a candle CLOSE that crosses the level by basis_bps.

    Distinct from sweep — `broken` requires a close, not just an intra-candle
    traversal. Used for SLQ / Main High invalidation.
    """
    distance = min_breach_distance(level, basis_bps)
    if direction == "up":
        return candle_close > level + distance
    return candle_close < level - distance


def trend_active(
    *,
    main_high: Decimal | None,
    main_low: Decimal | None,
    slq: Decimal | None,
    last_close: Decimal,
    confirmed_tlq_breaks_since_slq: int,
    direction: Trend,
) -> bool:
    """Trend-state validation per §6.2.11.

    DOWNTREND active iff:
        main_high.price > slq.price AND
        last_confirmed_close < slq.price AND
        count_confirmed_tlq_breaks_since_slq >= 1

    UPTREND: mirror.
    """
    if direction is Trend.NEW_TREND_PENDING:
        return False
    if slq is None:
        return False
    if confirmed_tlq_breaks_since_slq < 1:
        return False
    if direction is Trend.DOWN:
        if main_high is None or main_high <= slq:
            return False
        return last_close < slq
    if direction is Trend.UP:
        if main_low is None or main_low >= slq:
            return False
        return last_close > slq
    return False


def rejection_depth_bps(
    *,
    zone_top: Decimal,
    zone_bottom: Decimal,
    candle_high: Decimal,
    candle_low: Decimal,
    direction: Literal["SUPPLY", "DEMAND"],
) -> Decimal:
    """Depth in bps of a rejection wick into a zone.

    For SUPPLY (downtrend): how far ABOVE zone.bottom did the wick go,
        capped at zone.top — penetration / zone_bottom * 10_000.
    For DEMAND (uptrend): mirror — how far BELOW zone.top did the wick go,
        capped at zone.bottom.

    Zero if no penetration; large if wick reached the far side of the zone.
    """
    if zone_top < zone_bottom:
        raise ValueError("zone_top must be >= zone_bottom")
    if direction == "SUPPLY":
        if candle_high <= zone_bottom:
            return Decimal(0)
        capped = min(candle_high, zone_top)
        penetration = capped - zone_bottom
        return (penetration / zone_bottom) * Decimal(10_000) if zone_bottom > 0 else Decimal(0)
    # DEMAND
    if candle_low >= zone_top:
        return Decimal(0)
    capped = max(candle_low, zone_bottom)
    penetration = zone_top - capped
    return (penetration / zone_top) * Decimal(10_000) if zone_top > 0 else Decimal(0)


def depth_bucket_from_bps(
    bps: Decimal,
    *,
    shallow_max_bps: int = 5,
    medium_max_bps: int = 15,
) -> Literal["shallow", "medium", "deep"]:
    """Map rejection depth in bps to a bucket per the §1.5.1 paradox mitigation."""
    if bps < 0:
        raise ValueError("bps must be non-negative")
    if bps <= Decimal(shallow_max_bps):
        return "shallow"
    if bps <= Decimal(medium_max_bps):
        return "medium"
    return "deep"


def find_main_extremes(
    candles_high_low: list[tuple[int, Decimal, Decimal]],
) -> tuple[int, int]:
    """Return (high_idx, low_idx) — indices of the absolute high and low candles.

    Input: list of (idx, high, low) tuples. The cold-start procedure §6.2.9
    uses these to derive trend direction.
    """
    if not candles_high_low:
        raise ValueError("empty input")
    high_idx, _, _ = max(candles_high_low, key=lambda r: r[1])
    low_idx, _, _ = min(candles_high_low, key=lambda r: r[2])
    return high_idx, low_idx


def determine_cold_start_trend(
    *,
    main_high_ts: int,
    main_low_ts: int,
) -> Trend:
    """Per §6.2.9 STEP 2: trend direction = side of the *later* extreme.

    Note: §6.2.9 contains a typo. The blueprint reads
        ``If main_high.timestamp > main_low.timestamp: => current_trend = DOWN``
    but the narrative contradicts this: if main_high formed *later* than
    main_low (i.e. main_high.ts > main_low.ts), the most recent macro move
    was UP from low -> high, not down. We implement the logically correct
    behavior here:
        * main_low formed last  (main_low_ts > main_high_ts)  -> trend = DOWN
        * main_high formed last (main_high_ts > main_low_ts)  -> trend = UP
        * tie -> default DOWN (determinism).
    Recorded in the Phase 3 deviations log.
    """
    if main_low_ts > main_high_ts:
        return Trend.DOWN
    if main_high_ts > main_low_ts:
        return Trend.UP
    return Trend.DOWN
