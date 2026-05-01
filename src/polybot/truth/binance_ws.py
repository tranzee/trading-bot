"""Binance WS — primary BTC source.

Connects to wss://stream.binance.com:9443/stream with both
btcusdt@trade (every print) and btcusdt@kline_5m (one event per minute or so,
is_final=True at the 5-min close).

Auto-reconnect: 0.2s/0.4s/0.8s/... capped at 30s. Heartbeat watchdog: force
reconnect if no message for 5 s.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from typing import Any

import websockets

from polybot.obs.logger import log
from polybot.truth.models import BtcKlineClose, BtcSource, BtcTick


WS_BASE = "wss://stream.binance.com:9443"
DEFAULT_STREAM_PATH = "/stream?streams=btcusdt@trade/btcusdt@kline_5m"

WATCHDOG_SILENCE_S = 5.0
BACKOFF_BASE_S = 0.2
BACKOFF_CAP_S = 30.0


TickHandler = Callable[[BtcTick], None | Awaitable[None]]
KlineHandler = Callable[[BtcKlineClose], None | Awaitable[None]]


class BinanceWs:
    """Async Binance WS subscriber. Run with `await ws.start()`; stop with `await ws.stop()`.

    By default subscribes to BTC 5-minute klines. Pass `stream_path` to
    target a different asset/window (e.g. `/stream?streams=ethusdt@trade/ethusdt@kline_15m`).
    """

    def __init__(self, *, stream_path: str = DEFAULT_STREAM_PATH) -> None:
        self._tick_handlers: list[TickHandler] = []
        self._kline_handlers: list[KlineHandler] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._last_message_ms: int = 0
        self._url = WS_BASE + stream_path

    def add_tick_handler(self, fn: TickHandler) -> None:
        self._tick_handlers.append(fn)

    def add_kline_handler(self, fn: KlineHandler) -> None:
        self._kline_handlers.append(fn)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="binance-ws")

    async def wait_ready(self, timeout_s: float = 10.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def last_message_ms(self) -> int:
        return self._last_message_ms

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                delay = min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_CAP_S)
                log.warning(
                    "binance_ws: error {}; reconnect in {:.1f}s (attempt {})",
                    exc, delay, attempt,
                )
                self._connected.clear()
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise

    async def _session(self) -> None:
        async with websockets.connect(
            self._url, ping_interval=15, ping_timeout=15, close_timeout=2, max_size=2**22
        ) as ws:
            log.info("binance_ws: connected")
            self._connected.set()
            self._last_message_ms = int(time.time() * 1000)

            async def watchdog() -> None:
                while not self._stop.is_set():
                    await asyncio.sleep(2.0)
                    if int(time.time() * 1000) - self._last_message_ms > int(
                        WATCHDOG_SILENCE_S * 1000
                    ):
                        raise TimeoutError(
                            f"binance_ws: silence > {WATCHDOG_SILENCE_S}s"
                        )

            wd = asyncio.create_task(watchdog())
            try:
                async for raw in ws:
                    self._last_message_ms = int(time.time() * 1000)
                    await self._handle_raw(raw)
            finally:
                wd.cancel()
                try:
                    await wd
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def _handle_raw(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("binance_ws: bad JSON: {}", exc)
            return
        # Combined-stream payloads have shape {"stream": "...", "data": {...}}
        if isinstance(payload, dict) and "data" in payload:
            stream = str(payload.get("stream", ""))
            data = payload["data"]
        else:
            stream = ""
            data = payload
        if not isinstance(data, dict):
            return

        event_type = str(data.get("e", "")).lower()
        if event_type == "trade" or "trade" in stream:
            await self._emit_tick(data)
        elif event_type == "kline" or "kline" in stream:
            await self._emit_kline(data)

    async def _emit_tick(self, data: dict[str, Any]) -> None:
        try:
            tick = BtcTick(
                source=BtcSource.BINANCE,
                ts_ms=int(data.get("T") or data.get("E") or time.time() * 1000),
                price=Decimal(str(data["p"])),
                volume=Decimal(str(data["q"])),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("binance_ws: bad trade payload: {}", exc)
            return
        for fn in self._tick_handlers:
            await _maybe_await(fn(tick))

    async def _emit_kline(self, data: dict[str, Any]) -> None:
        k = data.get("k") or {}
        try:
            kline = BtcKlineClose(
                source=BtcSource.BINANCE,
                ts_ms=int(k["t"]),
                open=Decimal(str(k["o"])),
                high=Decimal(str(k["h"])),
                low=Decimal(str(k["l"])),
                close=Decimal(str(k["c"])),
                volume=Decimal(str(k["v"])),
                is_final=bool(k.get("x")),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("binance_ws: bad kline payload: {}", exc)
            return
        for fn in self._kline_handlers:
            await _maybe_await(fn(kline))


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value


async def stream_ticks(stop_event: asyncio.Event | None = None) -> AsyncIterator[BtcTick]:
    """Convenience: yield ticks until stop_event is set (or forever).

    Used by ad-hoc scripts; the engine uses the handler callbacks directly.
    """
    queue: asyncio.Queue[BtcTick] = asyncio.Queue(maxsize=1024)
    ws = BinanceWs()
    ws.add_tick_handler(queue.put_nowait)
    await ws.start()
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield tick
            except asyncio.TimeoutError:
                continue
    finally:
        await ws.stop()
