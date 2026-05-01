"""Coinbase Advanced Trade WS — divergence reference for BTC.

Channel: `market_trades`, product `BTC-USD`. Public, no auth required for
read-only market_trades. Same shape as the Binance class; only emits
BtcTick (no kline product on Coinbase Advanced Trade WS).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import websockets

from polybot.obs.logger import log
from polybot.truth.models import BtcSource, BtcTick


WS_URL = "wss://advanced-trade-ws.coinbase.com"
PRODUCT = "BTC-USD"
CHANNEL = "market_trades"

WATCHDOG_SILENCE_S = 15.0  # Coinbase market_trades is sparser than Binance trade
BACKOFF_BASE_S = 0.2
BACKOFF_CAP_S = 30.0


TickHandler = Callable[[BtcTick], None | Awaitable[None]]


class CoinbaseWs:
    def __init__(self, *, product: str = PRODUCT) -> None:
        self._tick_handlers: list[TickHandler] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._last_message_ms: int = 0
        self._product = product

    def add_tick_handler(self, fn: TickHandler) -> None:
        self._tick_handlers.append(fn)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="coinbase-ws")

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
                    "coinbase_ws: error {}; reconnect in {:.1f}s (attempt {})",
                    exc, delay, attempt,
                )
                self._connected.clear()
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise

    async def _session(self) -> None:
        async with websockets.connect(
            WS_URL, ping_interval=15, ping_timeout=15, close_timeout=2, max_size=2**22
        ) as ws:
            sub = {"type": "subscribe", "product_ids": [self._product], "channel": CHANNEL}
            await ws.send(json.dumps(sub))
            log.info("coinbase_ws: subscribed channel={} product={}", CHANNEL, self._product)
            self._connected.set()
            self._last_message_ms = int(time.time() * 1000)

            async def watchdog() -> None:
                while not self._stop.is_set():
                    await asyncio.sleep(3.0)
                    if int(time.time() * 1000) - self._last_message_ms > int(
                        WATCHDOG_SILENCE_S * 1000
                    ):
                        raise TimeoutError(
                            f"coinbase_ws: silence > {WATCHDOG_SILENCE_S}s"
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
            log.warning("coinbase_ws: bad JSON: {}", exc)
            return
        if not isinstance(payload, dict):
            return
        channel = str(payload.get("channel", ""))
        if channel != "market_trades":
            return
        events = payload.get("events") or []
        for ev in events:
            trades = ev.get("trades") if isinstance(ev, dict) else None
            for t in trades or []:
                await self._emit_trade(t)

    async def _emit_trade(self, t: dict[str, Any]) -> None:
        try:
            ts_str = str(t.get("time", ""))
            # ISO 8601: "2026-04-26T01:23:45.678Z"
            ts_ms = _parse_iso_to_ms(ts_str)
            tick = BtcTick(
                source=BtcSource.COINBASE,
                ts_ms=ts_ms,
                price=Decimal(str(t["price"])),
                volume=Decimal(str(t.get("size", "0"))),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("coinbase_ws: bad trade payload: {}", exc)
            return
        for fn in self._tick_handlers:
            await _maybe_await(fn(tick))


def _parse_iso_to_ms(iso: str) -> int:
    """Parse Coinbase's ISO-8601 timestamp into unix-ms."""
    if not iso:
        return int(time.time() * 1000)
    import datetime as dt

    # Coinbase uses 'Z' suffix and may include nanoseconds; truncate to micro.
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    # Trim sub-microsecond digits if present (datetime supports up to 6).
    if "." in iso:
        head, tail = iso.split(".", 1)
        # tail may look like '123456789+00:00' -> keep up to 6 digits before tz
        tz_split = max(tail.find("+"), tail.find("-"))
        if tz_split == -1:
            frac = tail
            tz = ""
        else:
            frac = tail[:tz_split]
            tz = tail[tz_split:]
        frac = (frac + "000000")[:6]
        iso = f"{head}.{frac}{tz}"
    return int(dt.datetime.fromisoformat(iso).timestamp() * 1000)


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value
