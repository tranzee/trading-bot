"""Unit tests for signal/math.py (the foundation of the PA engine)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polybot.signal import math as M
from polybot.signal.models import Trend


def test_min_breach_distance_basic() -> None:
    # 1 bp of $100,000 = $10
    assert M.min_breach_distance(Decimal("100000"), 1) == Decimal("10")
    # 5 bp of $50,000 = $25
    assert M.min_breach_distance(Decimal("50000"), 5) == Decimal("25")


def test_min_breach_distance_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        M.min_breach_distance(Decimal("0"), 1)
    with pytest.raises(ValueError):
        M.min_breach_distance(Decimal("100"), -1)


def test_touched() -> None:
    assert M.touched(Decimal("100"), Decimal("99"), Decimal("101"))
    assert M.touched(Decimal("100"), Decimal("100"), Decimal("100"))   # exact
    assert not M.touched(Decimal("100"), Decimal("101"), Decimal("102"))
    assert not M.touched(Decimal("100"), Decimal("97"), Decimal("99"))


def test_swept_above_below() -> None:
    # Swept above $100k by > 10 bps (1 bp on $100k = $10)
    assert M.swept(Decimal("100000"), "above", Decimal("100020"), basis_bps=1)
    assert not M.swept(Decimal("100000"), "above", Decimal("100005"), basis_bps=1)
    assert M.swept(Decimal("100000"), "below", Decimal("99950"), basis_bps=1)
    assert not M.swept(Decimal("100000"), "below", Decimal("99995"), basis_bps=1)


def test_broken_requires_distance() -> None:
    # Close at $100,011 breaks $100k UP at 1 bp ($10 distance)
    assert M.broken(Decimal("100000"), "up", Decimal("100011"), basis_bps=1)
    assert not M.broken(Decimal("100000"), "up", Decimal("100005"), basis_bps=1)


def test_trend_active_downtrend_happy() -> None:
    assert M.trend_active(
        main_high=Decimal("100000"),
        main_low=None,
        slq=Decimal("99000"),
        last_close=Decimal("98000"),
        confirmed_tlq_breaks_since_slq=2,
        direction=Trend.DOWN,
    )


def test_trend_active_downtrend_fails_above_slq() -> None:
    # Last close above SLQ -> trend not active (structural reversal in progress)
    assert not M.trend_active(
        main_high=Decimal("100000"),
        main_low=None,
        slq=Decimal("99000"),
        last_close=Decimal("99500"),
        confirmed_tlq_breaks_since_slq=2,
        direction=Trend.DOWN,
    )


def test_trend_active_pending_returns_false() -> None:
    assert not M.trend_active(
        main_high=Decimal("100000"),
        main_low=Decimal("99000"),
        slq=None,
        last_close=Decimal("100500"),
        confirmed_tlq_breaks_since_slq=0,
        direction=Trend.NEW_TREND_PENDING,
    )


def test_rejection_depth_supply_inside_zone() -> None:
    # Zone [99000, 99500]; wick to 99300; bottom = 99000
    # penetration = 99300 - 99000 = 300; bps = 300/99000 * 10000 = ~30.3
    bps = M.rejection_depth_bps(
        zone_top=Decimal("99500"),
        zone_bottom=Decimal("99000"),
        candle_high=Decimal("99300"),
        candle_low=Decimal("98500"),
        direction="SUPPLY",
    )
    assert bps > Decimal("30")
    assert bps < Decimal("31")


def test_rejection_depth_supply_no_touch_returns_zero() -> None:
    # Wick never reached zone bottom
    assert M.rejection_depth_bps(
        zone_top=Decimal("99500"),
        zone_bottom=Decimal("99000"),
        candle_high=Decimal("98800"),
        candle_low=Decimal("98500"),
        direction="SUPPLY",
    ) == Decimal(0)


def test_rejection_depth_demand_mirror() -> None:
    bps = M.rejection_depth_bps(
        zone_top=Decimal("99500"),
        zone_bottom=Decimal("99000"),
        candle_high=Decimal("99800"),
        candle_low=Decimal("99200"),
        direction="DEMAND",
    )
    # zone_top - max(candle_low, zone_bottom) = 99500 - 99200 = 300; /99500 * 10000 ~= 30.15
    assert bps > Decimal("30")
    assert bps < Decimal("31")


def test_depth_bucket_thresholds() -> None:
    assert M.depth_bucket_from_bps(Decimal("3")) == "shallow"
    assert M.depth_bucket_from_bps(Decimal("5")) == "shallow"   # boundary inclusive
    assert M.depth_bucket_from_bps(Decimal("10")) == "medium"
    assert M.depth_bucket_from_bps(Decimal("15")) == "medium"
    assert M.depth_bucket_from_bps(Decimal("16")) == "deep"
    assert M.depth_bucket_from_bps(Decimal("100")) == "deep"


def test_find_main_extremes() -> None:
    rows = [
        (0, Decimal("100"), Decimal("90")),
        (1, Decimal("110"), Decimal("95")),
        (2, Decimal("105"), Decimal("85")),     # absolute low here
        (3, Decimal("120"), Decimal("100")),    # absolute high here
    ]
    high_idx, low_idx = M.find_main_extremes(rows)
    assert high_idx == 3
    assert low_idx == 2


def test_cold_start_trend_direction() -> None:
    # Most recent extreme is main_high (ts=2000 > 1000) -> trend = UP
    assert M.determine_cold_start_trend(main_high_ts=2000, main_low_ts=1000) == Trend.UP
    # Most recent extreme is main_low -> trend = DOWN
    assert M.determine_cold_start_trend(main_high_ts=1000, main_low_ts=2000) == Trend.DOWN


def test_cold_start_trend_tie_defaults_down() -> None:
    assert M.determine_cold_start_trend(main_high_ts=1000, main_low_ts=1000) == Trend.DOWN
