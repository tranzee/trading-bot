"""5-min OHLCV aggregator aligned to wall clock (UTC).

Slot semantics:
    - Slots are 5-min UTC bins; floor(ts / 300_000) * 300_000 is slot OPEN.
    - A slot is `closed` when we receive the first tick whose ts_ms is in the
      next bin. We then emit a CandleClose event for the closed slot.
    - The in-progress (live) candle is exposed via `live_candle()` for the
      Alert-2 early-warning logic but NEVER added to the closed-history.

Source policy:
    - Aggregator consumes `BtcTick` events. By default it accepts ticks only
      from a primary source (default Binance) — this keeps the candle math
      single-source and reproducible.
    - If the primary source goes stale, the aggregator does NOT silently swap
      to a fallback; that's the ticker_tracker's circuit-breaker job. The
      aggregator simply pauses until the primary returns.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from config import constants as K
from polybot.obs.logger import log
from polybot.truth.models import BtcSource, BtcTick, Candle


def slot_open_ms(ts_ms: int, window_ms: int = K.SLOT_DURATION_MS) -> int:
    """Return the slot OPEN time that contains ts_ms (floor to window_ms)."""
    return (ts_ms // window_ms) * window_ms


CandleHandler = Callable[[Candle], None | Awaitable[None]]


class CandleAggregator:
    """Tick-driven OHLCV aggregator.

    The window is configurable per-instance via `window_seconds` (default 300
    for the legacy 5-minute slot). All slot-floor math uses this window so
    the aggregator can drive any timeframe (5m / 15m / 30m / 1h / ...).

    Public API:
        - on_tick(tick): feed an inbound BtcTick. Returns a Candle if a slot
          just closed because of this tick, else None.
        - live_candle() -> Candle | None: in-progress slot snapshot
        - closed_history(n) -> list[Candle]: most recent n closed candles
        - add_listener(fn): callback fired on every CandleClose
    """

    def __init__(
        self,
        *,
        primary_source: BtcSource = BtcSource.BINANCE,
        history_size: int = 1024,
        window_seconds: int = K.SLOT_DURATION_S,
    ) -> None:
        self._primary = primary_source
        self._history_size = history_size
        self._window_ms = window_seconds * 1000
        self._live: Candle | None = None
        self._closed: list[Candle] = []
        self._listeners: list[CandleHandler] = []

    @property
    def window_ms(self) -> int:
        return self._window_ms

    def add_listener(self, fn: CandleHandler) -> None:
        self._listeners.append(fn)

    def live_candle(self) -> Candle | None:
        return self._live

    def closed_history(self, n: int | None = None) -> list[Candle]:
        if n is None:
            return list(self._closed)
        return list(self._closed[-n:])

    def latest_closed(self) -> Candle | None:
        return self._closed[-1] if self._closed else None

    def on_tick(self, tick: BtcTick) -> Candle | None:
        """Process a tick. Returns the just-closed candle if a slot rolled over."""
        if tick.source != self._primary:
            return None
        bin_open = slot_open_ms(tick.ts_ms, self._window_ms)

        emitted: Candle | None = None
        if self._live is None:
            self._live = Candle(
                ts_ms=bin_open,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.volume,
                n_ticks=1,
            )
            return None

        if bin_open == self._live.ts_ms:
            self._live = self._live.with_tick(tick.price, tick.volume)
            return None

        if bin_open > self._live.ts_ms:
            # Slot rolled over. Close current, start new with this tick.
            emitted = self._live
            self._closed.append(emitted)
            if len(self._closed) > self._history_size:
                self._closed = self._closed[-self._history_size :]
            self._live = Candle(
                ts_ms=bin_open,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.volume,
                n_ticks=1,
            )
            self._fire_listeners(emitted)
            return emitted

        # bin_open < live.ts_ms: an out-of-order older tick. Reject it.
        log.warning(
            "candle_aggregator: out-of-order tick ts={} (current_open={}); skipping",
            tick.ts_ms,
            self._live.ts_ms,
        )
        return None

    def _fire_listeners(self, candle: Candle) -> None:
        for fn in self._listeners:
            try:
                result: Any = fn(candle)
                # If a listener returns a coroutine, fire-and-forget on the
                # current loop. Aggregators are typically called from sync
                # WS handlers, so we don't await here.
                if hasattr(result, "__await__"):
                    import asyncio

                    asyncio.ensure_future(result)
            except Exception as exc:  # noqa: BLE001
                log.warning("candle listener error: {}", exc)

    # Convenience: seed the aggregator from historical candles (cold-start §6.2.9)
    def seed_from_history(self, candles: list[Candle]) -> None:
        """Pre-load N closed candles before going live. The newest candle becomes
        history's tail; the live candle remains None until the first tick arrives."""
        if not candles:
            return
        self._closed = list(candles)[-self._history_size :]
        self._live = None
