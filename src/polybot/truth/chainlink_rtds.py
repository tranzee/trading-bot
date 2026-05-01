"""Polymarket RTDS — Chainlink BTC oracle prices.

Subscribes to wss://ws-live-data.polymarket.com/, channel `crypto_prices`.
This is RESOLUTION TRUTH: it's the oracle Polymarket uses to settle the
binary. We never use it for trade decisions — it's slower than the CEX
feeds — but the engine compares the slot-end Chainlink price against the
slot-start Chainlink price to compute price_to_beat and the final outcome.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import websockets

from config import constants as K
from polybot.obs.logger import log
from polybot.truth.models import BtcSource, BtcTick


WS_URL = K.POLYMARKET_WS_HOST  # wss://ws-live-data.polymarket.com/
CHANNEL = K.RTDS_CRYPTO_PRICES_CHANNEL  # "crypto_prices"

WATCHDOG_SILENCE_S = 30.0  # Chainlink updates are coarse (every few minutes)
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 30.0


TickHandler = Callable[[BtcTick], None | Awaitable[None]]


class ChainlinkRtds:
    """Chainlink BTC oracle subscriber via Polymarket RTDS."""

    def __init__(self) -> None:
        self._tick_handlers: list[TickHandler] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._last_message_ms: int = 0

    def add_tick_handler(self, fn: TickHandler) -> None:
        self._tick_handlers.append(fn)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="chainlink-rtds")

    async def wait_ready(self, timeout_s: float = 30.0) -> bool:
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
                    "rtds: error {}; reconnect in {:.1f}s (attempt {})",
                    exc, delay, attempt,
                )
                self._connected.clear()
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise

    async def _session(self) -> None:
        async with websockets.connect(
            WS_URL, ping_interval=20, ping_timeout=20, close_timeout=2, max_size=2**22
        ) as ws:
            sub = {"type": "subscribe", "channel": CHANNEL}
            await ws.send(json.dumps(sub))
            log.info("rtds: subscribed channel={}", CHANNEL)
            self._connected.set()
            self._last_message_ms = int(time.time() * 1000)

            async def watchdog() -> None:
                while not self._stop.is_set():
                    await asyncio.sleep(5.0)
                    if int(time.time() * 1000) - self._last_message_ms > int(
                        WATCHDOG_SILENCE_S * 1000
                    ):
                        raise TimeoutError(f"rtds: silence > {WATCHDOG_SILENCE_S}s")

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
            log.warning("rtds: bad JSON: {}", exc)
            return

        if isinstance(payload, list):
            for event in payload:
                if isinstance(event, dict):
                    await self._handle_event(event)
        elif isinstance(payload, dict):
            await self._handle_event(payload)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        # We accept either a flat tick or an enveloped one.
        symbol = str(
            event.get("symbol")
            or event.get("asset")
            or event.get("ticker")
            or ""
        ).upper()
        if symbol and "BTC" not in symbol:
            return
        price_str = (
            event.get("price")
            or event.get("answer")
            or event.get("value")
            or event.get("p")
        )
        if price_str is None:
            return
        try:
            price = Decimal(str(price_str))
        except Exception:  # noqa: BLE001
            return

        ts_ms = int(event.get("ts") or event.get("timestamp") or time.time() * 1000)
        tick = BtcTick(
            source=BtcSource.CHAINLINK, ts_ms=ts_ms, price=price, volume=Decimal(0)
        )
        for fn in self._tick_handlers:
            await _maybe_await(fn(tick))


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value
