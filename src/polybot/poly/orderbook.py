"""WebSocket order book tracker for Polymarket CLOB V2.

The SDK is REST-only. We subscribe to Polymarket's public market WS at
`wss://ws-subscriptions-clob.polymarket.com/ws/market` for the `book`
channel and keep an in-memory L2 book per token. Provides:

    - best_bid / best_ask / mid / spread_bps
    - BookUpdate event stream for the maker engine
    - auto-reconnect with exponential backoff
    - heartbeat watchdog (force reconnect if quiet > 10 s)

Does NOT do user/order events — that's a different channel; Phase 5 wires
that up. Phase 1 only needs the public book.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import websockets

from polybot.obs.logger import log
from polybot.poly.order_dsl import BookLevel, OrderBookSnapshot


WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

#: Watchdog: force reconnect if no message for this long.
WATCHDOG_SILENCE_S = 10.0

#: Reconnect backoff: 0.2, 0.4, 0.8, ... capped at 30s.
BACKOFF_BASE_S = 0.2
BACKOFF_CAP_S = 30.0


@dataclass(slots=True, frozen=True)
class BookUpdate:
    """Emitted whenever a tracked book changes materially (best level move or size delta)."""

    token_id: str
    snapshot: OrderBookSnapshot
    received_at_ms: int


@dataclass
class _BookState:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_update_ms: int = 0

    def snapshot(self, token_id: str) -> OrderBookSnapshot:
        bids = tuple(
            sorted(
                (BookLevel(price=p, size=s) for p, s in self.bids.items() if s > 0),
                key=lambda lvl: lvl.price,
                reverse=True,
            )
        )
        asks = tuple(
            sorted(
                (BookLevel(price=p, size=s) for p, s in self.asks.items() if s > 0),
                key=lambda lvl: lvl.price,
            )
        )
        return OrderBookSnapshot(
            token_id=token_id, bids=bids, asks=asks, timestamp_ms=self.last_update_ms
        )

    def apply_snapshot(self, raw: dict[str, Any]) -> None:
        self.bids = {Decimal(str(b["price"])): Decimal(str(b["size"])) for b in raw.get("bids", [])}
        self.asks = {Decimal(str(a["price"])): Decimal(str(a["size"])) for a in raw.get("asks", [])}

    def apply_delta(self, raw: dict[str, Any]) -> None:
        for change in raw.get("changes") or []:
            side = str(change.get("side", "")).upper()
            price = Decimal(str(change["price"]))
            size = Decimal(str(change["size"]))
            book = self.bids if side == "BUY" else self.asks
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size


class OrderBookTracker:
    """One-tracker-per-process; subscribes to N tokens and emits BookUpdate events."""

    def __init__(self) -> None:
        self._books: dict[str, _BookState] = {}
        self._subscribers: list[Callable[[BookUpdate], None]] = []
        self._token_ids: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_message_ms: int = 0
        self._connected = asyncio.Event()

    def subscribe_token(self, token_id: str) -> None:
        self._token_ids.add(token_id)
        self._books.setdefault(token_id, _BookState())

    def add_listener(self, cb: Callable[[BookUpdate], None]) -> None:
        self._subscribers.append(cb)

    def snapshot(self, token_id: str) -> OrderBookSnapshot | None:
        state = self._books.get(token_id)
        if state is None or state.last_update_ms == 0:
            return None
        return state.snapshot(token_id)

    def best_bid(self, token_id: str) -> BookLevel | None:
        snap = self.snapshot(token_id)
        return snap.best_bid() if snap else None

    def best_ask(self, token_id: str) -> BookLevel | None:
        snap = self.snapshot(token_id)
        return snap.best_ask() if snap else None

    def mid(self, token_id: str) -> Decimal | None:
        snap = self.snapshot(token_id)
        return snap.mid() if snap else None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="orderbook-tracker")

    async def wait_ready(self, timeout_s: float = 5.0) -> bool:
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
                delay = min(BACKOFF_BASE_S * 2 ** (attempt - 1), BACKOFF_CAP_S)
                log.warning(
                    "orderbook_ws: error {}; reconnecting in {:.1f}s (attempt {})",
                    exc, delay, attempt,
                )
                self._connected.clear()
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise

    async def _session(self) -> None:
        if not self._token_ids:
            log.warning("orderbook_ws: no tokens subscribed; sleeping")
            await asyncio.sleep(1.0)
            return
        async with websockets.connect(
            WS_MARKET_URL,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=2,
            max_size=2**22,
        ) as ws:
            sub_msg = {"type": "subscribe", "channel": "market", "assets_ids": list(self._token_ids)}
            await ws.send(json.dumps(sub_msg))
            log.info("orderbook_ws: subscribed to {} tokens", len(self._token_ids))
            self._connected.set()
            self._last_message_ms = int(time.time() * 1000)

            async def watchdog() -> None:
                while not self._stop.is_set():
                    await asyncio.sleep(2.0)
                    if int(time.time() * 1000) - self._last_message_ms > int(WATCHDOG_SILENCE_S * 1000):
                        raise TimeoutError(
                            f"watchdog: no WS message in {WATCHDOG_SILENCE_S:.0f}s"
                        )

            wd_task = asyncio.create_task(watchdog())
            try:
                async for raw in ws:
                    self._last_message_ms = int(time.time() * 1000)
                    self._handle_message(raw)
            finally:
                wd_task.cancel()
                try:
                    await wd_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("orderbook_ws: bad JSON: {}", exc)
            return

        # Polymarket WS sends arrays of events or single events; normalize.
        if isinstance(payload, list):
            for event in payload:
                self._handle_event(event)
        elif isinstance(payload, dict):
            self._handle_event(payload)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or event.get("type") or "").lower()
        token_id = str(event.get("asset_id") or event.get("token_id") or "")
        if not token_id:
            return
        state = self._books.setdefault(token_id, _BookState())

        if event_type in ("book", "orderbook", "snapshot"):
            state.apply_snapshot(event)
        elif event_type in ("price_change", "delta"):
            state.apply_delta(event)
        else:
            return

        state.last_update_ms = int(time.time() * 1000)
        snap = state.snapshot(token_id)
        update = BookUpdate(token_id=token_id, snapshot=snap, received_at_ms=state.last_update_ms)
        for cb in self._subscribers:
            try:
                cb(update)
            except Exception as exc:  # noqa: BLE001
                log.warning("orderbook listener error: {}", exc)
