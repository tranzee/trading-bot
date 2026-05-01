"""Tests proving the truth/HTF layers are window-agnostic (multi-config refactor)."""

from decimal import Decimal

from polybot.signal.htf_filter import HtfFilter, _aggregate_to_window
from polybot.truth.candle_aggregator import CandleAggregator, slot_open_ms
from polybot.truth.models import BtcSource, BtcTick, Candle


def _candle(ts_ms: int, close: Decimal) -> Candle:
    return Candle(
        ts_ms=ts_ms,
        open=close, high=close, low=close, close=close,
        volume=Decimal(0), n_ticks=1,
    )


class TestSlotOpenMs:
    def test_5m_floor_matches_arithmetic(self):
        ts = 1_700_000_123_000
        assert slot_open_ms(ts, 300_000) == (ts // 300_000) * 300_000

    def test_15m_floor_matches_arithmetic(self):
        ts = 1_700_000_523_000
        assert slot_open_ms(ts, 900_000) == (ts // 900_000) * 900_000

    def test_default_window_is_5m(self):
        ts = 1_700_000_523_000
        assert slot_open_ms(ts) == (ts // 300_000) * 300_000

    def test_15m_floor_is_coarser_than_5m(self):
        ts = 1_700_000_523_000
        five = slot_open_ms(ts, 300_000)
        fifteen = slot_open_ms(ts, 900_000)
        assert fifteen <= five  # coarser bin starts no later than the finer one


class TestCandleAggregator:
    def test_5m_window_default(self):
        agg = CandleAggregator()
        assert agg.window_ms == 300_000

    def test_15m_window(self):
        agg = CandleAggregator(window_seconds=900)
        assert agg.window_ms == 900_000

    def test_15m_aggregates_ticks_into_one_slot(self):
        agg = CandleAggregator(window_seconds=900)
        # Align base to a 15m boundary so ticks at +0, +5m, +10m all fall in the same slot
        base = 1_700_000_000_000 // 900_000 * 900_000
        for offset in (0, 300_000, 600_000):
            agg.on_tick(BtcTick(
                source=BtcSource.BINANCE,
                ts_ms=base + offset,
                price=Decimal("100"),
                volume=Decimal("1"),
            ))
        assert agg.live_candle() is not None
        # All three ticks are in the same 15m slot; nothing closed yet
        assert len(agg.closed_history()) == 0

    def test_15m_closes_when_next_slot_starts(self):
        agg = CandleAggregator(window_seconds=900)
        base = 1_700_000_000_000 // 900_000 * 900_000
        agg.on_tick(BtcTick(source=BtcSource.BINANCE, ts_ms=base,
                            price=Decimal("100"), volume=Decimal("1")))
        # Tick into the next 15m slot
        emitted = agg.on_tick(BtcTick(
            source=BtcSource.BINANCE, ts_ms=base + 900_000,
            price=Decimal("101"), volume=Decimal("1"),
        ))
        assert emitted is not None
        assert emitted.ts_ms == base
        assert len(agg.closed_history()) == 1


class TestHtfFilter:
    def test_default_aggregates_to_1h(self):
        f = HtfFilter()
        assert f._htf_window_ms == 3_600_000

    def test_custom_htf_window(self):
        # 5m × 12 = 1h (default), but for 15m we want 12h:
        f = HtfFilter(htf_window_ms=12 * 900 * 1000)
        assert f._htf_window_ms == 10_800_000

    def test_aggregate_to_window_buckets(self):
        # 4 candles at 0s, 5m, 10m, 15m at a 15m HTF window:
        #   first 3 fall into bin 0, the 4th falls into bin 1
        candles = [
            _candle(0,         Decimal("100")),
            _candle(300_000,   Decimal("101")),
            _candle(600_000,   Decimal("102")),
            _candle(900_000,   Decimal("103")),
        ]
        bins = _aggregate_to_window(candles, 900_000)
        assert len(bins) == 2
        assert bins[0].open == Decimal("100") and bins[0].close == Decimal("102")
        assert bins[1].open == Decimal("103")
