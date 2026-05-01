"""Truth-layer data carriers — ticks and candles.

These are immutable, lightweight, and source-tagged so the ticker_tracker
can attribute divergence and stale-data conditions to the right source.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class BtcSource(str, Enum):
    """Where a tick or candle came from."""

    BINANCE = "binance"
    COINBASE = "coinbase"
    CHAINLINK = "chainlink"


@dataclass(slots=True, frozen=True)
class BtcTick:
    """One BTC trade event from a CEX WS stream."""

    source: BtcSource
    ts_ms: int                 # exchange timestamp if available, else local rx time
    price: Decimal
    volume: Decimal            # may be 0 for tickless oracle sources


@dataclass(slots=True, frozen=True)
class BtcKlineClose:
    """A 5-min Binance kline event. is_final=False for in-progress, True at close."""

    source: BtcSource
    ts_ms: int                 # candle OPEN time
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_final: bool


@dataclass(slots=True, frozen=True)
class Candle:
    """A 5-min OHLCV candle assembled by candle_aggregator from raw ticks.

    `ts_ms` is the candle OPEN time (always divisible by SLOT_DURATION_MS).
    """

    ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    n_ticks: int

    @property
    def end_ms(self) -> int:
        from config import constants as K

        return self.ts_ms + K.SLOT_DURATION_MS

    def with_tick(self, price: Decimal, volume: Decimal) -> "Candle":
        return Candle(
            ts_ms=self.ts_ms,
            open=self.open,
            high=max(self.high, price),
            low=min(self.low, price),
            close=price,
            volume=self.volume + volume,
            n_ticks=self.n_ticks + 1,
        )
