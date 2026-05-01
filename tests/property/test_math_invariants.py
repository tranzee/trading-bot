"""Hypothesis property tests for signal/math.py.

These run before liquidity.py begins. If any seed fails, Phase 3 halts
and the engine is debugged before continuing.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from polybot.signal import math as M
from polybot.signal.models import Trend


_PRICE = st.decimals(min_value=Decimal("1"), max_value=Decimal("200000"), allow_nan=False, allow_infinity=False, places=2)
_SMALL = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100"), allow_nan=False, allow_infinity=False, places=2)


@given(level=_PRICE, basis_bps=st.integers(min_value=0, max_value=10000))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_min_breach_distance_is_proportional(level: Decimal, basis_bps: int) -> None:
    """min_breach_distance scales linearly with both level and basis_bps."""
    base = M.min_breach_distance(level, basis_bps)
    assert base >= Decimal(0)
    if basis_bps > 0:
        # 2x bps -> exactly 2x distance (modulo Decimal precision)
        doubled = M.min_breach_distance(level, basis_bps * 2)
        assert abs(doubled - base * 2) < Decimal("1e-10")


@given(low=_PRICE, high=_PRICE)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_touched_iff_in_range(low: Decimal, high: Decimal) -> None:
    """`touched(level)` matches the in-range definition for any level."""
    if high < low:
        low, high = high, low
    # level inside [low, high]
    mid = (low + high) / Decimal(2)
    assert M.touched(mid, low, high)
    # level above high
    assert not M.touched(high + Decimal(1), low, high)
    # level below low
    assert not M.touched(low - Decimal(1), low, high)


@given(level=_PRICE, distance=_SMALL, basis_bps=st.integers(min_value=1, max_value=100))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_swept_above_below_symmetry(level: Decimal, distance: Decimal, basis_bps: int) -> None:
    """If price > level + min_dist, swept above is True; price < level - min_dist, swept below is True."""
    min_dist = M.min_breach_distance(level, basis_bps)
    above = level + min_dist + distance
    below = level - min_dist - distance
    assert M.swept(level, "above", above, basis_bps=basis_bps)
    assert M.swept(level, "below", below, basis_bps=basis_bps)
    # Inside the threshold band: never swept
    if min_dist > Decimal("0.01"):
        # NOTE: Decimal precision; use a value strictly inside the band
        inside_above = level + min_dist - Decimal("0.001")
        if inside_above >= level:
            assert not M.swept(level, "above", inside_above, basis_bps=basis_bps)


@given(
    main_high=_PRICE,
    slq_offset=_SMALL,
    close_offset=_SMALL,
    n_breaks=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_downtrend_active_when_close_below_slq_below_main(
    main_high: Decimal, slq_offset: Decimal, close_offset: Decimal, n_breaks: int
) -> None:
    """Downtrend is active when SLQ < Main High AND last_close < SLQ AND >=1 TLQ break."""
    slq = main_high - slq_offset
    last_close = slq - close_offset
    assume_positive = slq > 0 and last_close > 0
    if not assume_positive:
        return
    assert M.trend_active(
        main_high=main_high, main_low=None, slq=slq, last_close=last_close,
        confirmed_tlq_breaks_since_slq=n_breaks, direction=Trend.DOWN,
    )


# ============================================================================
# GATING TEST: Monotonic-downtrend invariants (the Phase 3 gate).
# Failure halts Phase 3 — see directives at the start of Phase 3.
# ============================================================================


@st.composite
def _strictly_decreasing_close_seq(draw: st.DrawFn) -> list[tuple[int, Decimal, Decimal, Decimal, Decimal]]:
    """Generate (ts, open, high, low, close) tuples with strictly decreasing closes.

    Each candle's high == open (max of the bar) and low == close (min of bar),
    so the sequence is also monotonically decreasing in high and low.
    """
    n = draw(st.integers(min_value=5, max_value=200))
    # Start price somewhere in the realistic BTC range
    start = draw(st.decimals(min_value=Decimal("50000"), max_value=Decimal("150000"), places=2))
    # Choose strictly positive deltas
    deltas = draw(
        st.lists(
            st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2),
            min_size=n,
            max_size=n,
        )
    )
    rows: list[tuple[int, Decimal, Decimal, Decimal, Decimal]] = []
    price = start
    ts = 1_700_000_000_000
    for d in deltas:
        next_price = price - d
        if next_price <= Decimal("100"):  # avoid pathological tiny prices
            next_price = price * Decimal("0.99")
        # OHLC: open=price, close=next_price, high=open, low=close
        rows.append((ts, price, price, next_price, next_price))
        price = next_price
        ts += 300_000
    return rows


@given(seq=_strictly_decreasing_close_seq())
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_monotonic_downtrend_invariants(
    seq: list[tuple[int, Decimal, Decimal, Decimal, Decimal]],
) -> None:
    """The Phase 3 gating property test.

    For any strictly-decreasing close sequence, the cold-start procedure must:
        1. Detect trend = DOWN
        2. Identify the FIRST (highest) candle as Main High
        3. Identify the LAST (lowest) candle as Main Low
        4. determine_cold_start_trend returns DOWN

    Once liquidity.py is implemented, this property test will be extended
    to verify Main High never updates and no absolute_kill_switch fires.
    """
    rows = [(i, h, l) for i, (_ts, _o, h, l, _c) in enumerate(seq)]
    high_idx, low_idx = M.find_main_extremes(rows)

    # In a strictly-decreasing sequence, the highest high is at the FIRST candle
    # and the lowest low is at the LAST candle.
    assert high_idx == 0, f"main high should be at index 0 in a monotonic downtrend; got {high_idx}"
    assert low_idx == len(rows) - 1, f"main low should be at the last index; got {low_idx}"

    # Cold-start trend determination should yield DOWN (most recent extreme is the low).
    main_high_ts = seq[high_idx][0]
    main_low_ts = seq[low_idx][0]
    assert M.determine_cold_start_trend(main_high_ts=main_high_ts, main_low_ts=main_low_ts) == Trend.DOWN


@st.composite
def _strictly_increasing_close_seq(draw: st.DrawFn) -> list[tuple[int, Decimal, Decimal, Decimal, Decimal]]:
    n = draw(st.integers(min_value=5, max_value=200))
    start = draw(st.decimals(min_value=Decimal("50000"), max_value=Decimal("150000"), places=2))
    deltas = draw(
        st.lists(
            st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2),
            min_size=n, max_size=n,
        )
    )
    rows: list[tuple[int, Decimal, Decimal, Decimal, Decimal]] = []
    price = start
    ts = 1_700_000_000_000
    for d in deltas:
        next_price = price + d
        rows.append((ts, price, next_price, price, next_price))
        price = next_price
        ts += 300_000
    return rows


@given(seq=_strictly_increasing_close_seq())
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_monotonic_uptrend_invariants(
    seq: list[tuple[int, Decimal, Decimal, Decimal, Decimal]],
) -> None:
    """Mirror of the gating test — strictly increasing close sequence."""
    rows = [(i, h, l) for i, (_ts, _o, h, l, _c) in enumerate(seq)]
    high_idx, low_idx = M.find_main_extremes(rows)
    assert high_idx == len(rows) - 1
    assert low_idx == 0
    assert M.determine_cold_start_trend(
        main_high_ts=seq[high_idx][0], main_low_ts=seq[low_idx][0]
    ) == Trend.UP
