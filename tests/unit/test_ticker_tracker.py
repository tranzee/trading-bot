"""TickerTracker — consensus, divergence, and health policy."""

from __future__ import annotations

import time
from decimal import Decimal

from polybot.truth.models import BtcSource, BtcTick
from polybot.truth.ticker_tracker import TickerTracker


def _tick(source: BtcSource, ts_ms: int, price: str) -> BtcTick:
    return BtcTick(source=source, ts_ms=ts_ms, price=Decimal(price), volume=Decimal(0))


def test_single_source_is_consensus() -> None:
    t = TickerTracker()
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    assert t.get_price() == Decimal("100000")


def test_two_sources_average_via_median() -> None:
    t = TickerTracker()
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    t.on_tick(_tick(BtcSource.COINBASE, now_ms, "100050"))
    # Two sources -> tie-break to Binance per spec
    assert t.get_price() == Decimal("100000")


def test_divergence_bps_calculation() -> None:
    t = TickerTracker()
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    t.on_tick(_tick(BtcSource.COINBASE, now_ms, "100100"))  # 100 USD spread
    # consensus = Binance = 100000; spread/consensus * 10000 = 100/100000 * 10000 = 10 bps
    assert t.divergence_bps() == Decimal(10)


def test_chainlink_excluded_from_consensus() -> None:
    t = TickerTracker()
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    t.on_tick(_tick(BtcSource.CHAINLINK, now_ms, "99000"))  # 1% off
    # Chainlink not used for trade-decision consensus
    assert t.get_price() == Decimal("100000")
    # Divergence ignores Chainlink too (only one CEX -> None)
    assert t.divergence_bps() is None


def test_health_unhealthy_when_required_source_stale() -> None:
    t = TickerTracker(stale_timeout_s=1.0)
    old_ms = int(time.time() * 1000) - 5_000
    t.on_tick(_tick(BtcSource.BINANCE, old_ms, "100000"))
    h = t.is_healthy()
    assert not h.healthy
    assert "stale" in h.reason.lower()


def test_health_unhealthy_when_divergence_breach_persists() -> None:
    t = TickerTracker(divergence_threshold_bps=Decimal(10), divergence_grace_s=0.0)
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    t.on_tick(_tick(BtcSource.COINBASE, now_ms, "100200"))  # 20 bps
    # Grace = 0 -> immediate trip
    h = t.is_healthy()
    assert not h.healthy
    assert "divergence" in h.reason.lower()


def test_health_healthy_under_threshold() -> None:
    t = TickerTracker(divergence_threshold_bps=Decimal(50))
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    t.on_tick(_tick(BtcSource.COINBASE, now_ms, "100050"))  # 5 bps
    assert t.is_healthy().healthy


def test_out_of_order_tick_ignored() -> None:
    t = TickerTracker()
    now_ms = int(time.time() * 1000)
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "100000"))
    # older tick
    t.on_tick(_tick(BtcSource.BINANCE, now_ms - 1000, "99000"))
    assert t.get_price() == Decimal("100000")


def test_divergence_skips_stale_source_during_reconnect() -> None:
    """Regression: a stale cached tick must NOT inflate divergence_bps.

    Reproduces the issue observed in the live Phase 2 run: when Coinbase WS
    hit a keepalive timeout, its last cached tick stayed in the tracker and
    produced a spurious 7.44 bps divergence spike vs. fresh Binance ticks.
    """
    t = TickerTracker(stale_timeout_s=1.0)
    now_ms = int(time.time() * 1000)
    # Coinbase tick from 5 seconds ago (stale)
    t.on_tick(_tick(BtcSource.COINBASE, now_ms - 5_000, "78000"))
    # Fresh Binance tick has moved 60 USD higher since then
    t.on_tick(_tick(BtcSource.BINANCE, now_ms, "78060"))
    # Divergence should be None: only one fresh source available
    assert t.divergence_bps() is None
    # is_healthy() correctly flags Coinbase as stale
    assert not t.is_healthy().healthy
