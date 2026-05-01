"""Candle aggregator — wall-clock-aligned 5-min OHLCV semantics."""

from __future__ import annotations

from decimal import Decimal

from config import constants as K
from polybot.truth.candle_aggregator import CandleAggregator, slot_open_ms
from polybot.truth.models import BtcSource, BtcTick


def _tick(ts_ms: int, price: str, volume: str = "1") -> BtcTick:
    return BtcTick(source=BtcSource.BINANCE, ts_ms=ts_ms, price=Decimal(price), volume=Decimal(volume))


def test_slot_open_floors_to_5min_grid() -> None:
    assert slot_open_ms(0) == 0
    assert slot_open_ms(299_999) == 0
    assert slot_open_ms(300_000) == 300_000
    assert slot_open_ms(300_001) == 300_000


def test_first_tick_starts_live_candle() -> None:
    agg = CandleAggregator()
    out = agg.on_tick(_tick(1_000_000, "100"))
    assert out is None
    live = agg.live_candle()
    assert live is not None
    assert live.ts_ms == slot_open_ms(1_000_000)
    assert live.open == live.high == live.low == live.close == Decimal("100")
    assert live.n_ticks == 1


def test_candle_close_emitted_on_slot_rollover() -> None:
    agg = CandleAggregator()
    bin_open = 1_777_167_000_000  # divisible by 300_000
    # Build a candle of 3 ticks, then a tick in the next slot
    agg.on_tick(_tick(bin_open + 1_000, "100", "1"))
    agg.on_tick(_tick(bin_open + 60_000, "105", "2"))
    agg.on_tick(_tick(bin_open + 240_000, "98", "1"))
    closed = agg.on_tick(_tick(bin_open + K.SLOT_DURATION_MS + 1_000, "99", "1"))
    assert closed is not None
    assert closed.ts_ms == bin_open
    assert closed.open == Decimal("100")
    assert closed.high == Decimal("105")
    assert closed.low == Decimal("98")
    assert closed.close == Decimal("98")
    assert closed.volume == Decimal("4")
    assert closed.n_ticks == 3
    # New live candle started
    live = agg.live_candle()
    assert live is not None and live.ts_ms == bin_open + K.SLOT_DURATION_MS


def test_candle_listener_fires() -> None:
    agg = CandleAggregator()
    received: list[tuple[int, Decimal]] = []
    agg.add_listener(lambda c: received.append((c.ts_ms, c.close)))

    bin_open = 600_000
    agg.on_tick(_tick(bin_open + 1_000, "10"))
    agg.on_tick(_tick(bin_open + K.SLOT_DURATION_MS + 1_000, "11"))
    assert received == [(bin_open, Decimal("10"))]


def test_aggregator_ignores_non_primary_source() -> None:
    agg = CandleAggregator(primary_source=BtcSource.BINANCE)
    coinbase_tick = BtcTick(
        source=BtcSource.COINBASE,
        ts_ms=1_000_000,
        price=Decimal("100"),
        volume=Decimal(1),
    )
    out = agg.on_tick(coinbase_tick)
    assert out is None
    assert agg.live_candle() is None  # never started — primary not seen


def test_out_of_order_tick_dropped() -> None:
    agg = CandleAggregator()
    bin_open = 1_777_167_000_000
    agg.on_tick(_tick(bin_open + 60_000, "100"))
    # tick from a previous slot
    out = agg.on_tick(_tick(bin_open - 1_000, "50"))
    assert out is None
    live = agg.live_candle()
    assert live is not None and live.ts_ms == bin_open
    # historical low should not be polluted by the dropped tick
    assert live.low == Decimal("100")


def test_seed_from_history() -> None:
    from polybot.truth.models import Candle

    seed = [
        Candle(ts_ms=i * K.SLOT_DURATION_MS, open=Decimal("100"), high=Decimal("100"),
               low=Decimal("100"), close=Decimal("100"), volume=Decimal("1"), n_ticks=1)
        for i in range(5)
    ]
    agg = CandleAggregator()
    agg.seed_from_history(seed)
    assert len(agg.closed_history()) == 5
    assert agg.live_candle() is None
