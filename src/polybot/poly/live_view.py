"""Live order book viewer — implements `polybot live-orderbook`.

Resolves the next BTC 5m slot, subscribes to its UP/DOWN token book WS,
prints a refreshing book snapshot to the terminal. Phase 1 acceptance.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

from rich.console import Console
from rich.live import Live
from rich.table import Table

from polybot.obs.logger import log
from polybot.poly.client import PolyClient
from polybot.poly.market_discovery import resolve_next_slot
from polybot.poly.order_dsl import MarketHandle, OrderBookSnapshot
from polybot.poly.orderbook import OrderBookTracker


def _format_levels(snap: OrderBookSnapshot | None, n: int = 5) -> list[tuple[str, str, str, str]]:
    """Top-N bid/ask rows: (bid_size, bid_price, ask_price, ask_size)."""
    if snap is None:
        return [("-", "-", "-", "-")]
    bids = snap.bids[:n]
    asks = snap.asks[:n]
    rows: list[tuple[str, str, str, str]] = []
    for i in range(max(len(bids), len(asks))):
        b = bids[i] if i < len(bids) else None
        a = asks[i] if i < len(asks) else None
        rows.append(
            (
                f"{b.size}" if b else "",
                f"{b.price}" if b else "",
                f"{a.price}" if a else "",
                f"{a.size}" if a else "",
            )
        )
    return rows


def _build_table(handle: MarketHandle, tracker: OrderBookTracker, started_ms: int) -> Table:
    table = Table(title=f"Polymarket {handle.slug}", title_style="bold")
    table.add_column("UP bid sz", justify="right")
    table.add_column("UP bid", justify="right")
    table.add_column("UP ask", justify="right")
    table.add_column("UP ask sz", justify="right")
    table.add_column("|", justify="center", style="dim")
    table.add_column("DN bid sz", justify="right")
    table.add_column("DN bid", justify="right")
    table.add_column("DN ask", justify="right")
    table.add_column("DN ask sz", justify="right")

    up_id, down_id = handle.token_ids
    up_rows = _format_levels(tracker.snapshot(up_id))
    dn_rows = _format_levels(tracker.snapshot(down_id))
    for i in range(max(len(up_rows), len(dn_rows))):
        u = up_rows[i] if i < len(up_rows) else ("", "", "", "")
        d = dn_rows[i] if i < len(dn_rows) else ("", "", "", "")
        table.add_row(u[0], u[1], u[2], u[3], "|", d[0], d[1], d[2], d[3])

    elapsed_s = max(0, int(time.time() * 1000) - started_ms) / 1000
    slot_remaining_s = max(0, (handle.slot_end_ms - int(time.time() * 1000)) / 1000)
    up_mid = tracker.mid(up_id)
    dn_mid = tracker.mid(down_id)
    sum_mid: Decimal | None = None
    if up_mid is not None and dn_mid is not None:
        sum_mid = up_mid + dn_mid

    table.caption = (
        f"slot ends in {slot_remaining_s:5.1f}s | "
        f"running {elapsed_s:5.1f}s | "
        f"UP_mid={up_mid} DN_mid={dn_mid} "
        f"{'(sum=' + str(sum_mid) + ')' if sum_mid is not None else ''}"
    )
    return table


async def run_live_orderbook(duration_s: int = 60, slot_offset: int = 0) -> int:
    from config.settings import load_settings

    settings = load_settings()
    poly = PolyClient(settings)
    await poly.setup_creds()

    log.info("live_orderbook: resolving market handle (slot_offset={})", slot_offset)
    handle = await resolve_next_slot(poly, slot_offset=slot_offset)
    log.info(
        "live_orderbook: {} condition={} tokens={} tick={}",
        handle.slug,
        handle.condition_id,
        handle.token_ids,
        handle.tick_size,
    )

    tracker = OrderBookTracker()
    for tid in handle.token_ids:
        tracker.subscribe_token(tid)
    await tracker.start()
    ready = await tracker.wait_ready(timeout_s=10)
    if not ready:
        log.error("live_orderbook: tracker did not become ready in 10s")
        await tracker.stop()
        return 2

    started_ms = int(time.time() * 1000)
    deadline = time.monotonic() + duration_s
    console = Console()

    try:
        with Live(
            _build_table(handle, tracker, started_ms),
            console=console,
            refresh_per_second=4,
        ) as live:
            while time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                live.update(_build_table(handle, tracker, started_ms))
    finally:
        await tracker.stop()

    # Final integrity check (Phase 1 acceptance criterion):
    up_id, down_id = handle.token_ids
    up_snap = tracker.snapshot(up_id)
    dn_snap = tracker.snapshot(down_id)

    def _has_two_sided(s: OrderBookSnapshot | None) -> bool:
        return s is not None and bool(s.bids) and bool(s.asks)

    if _has_two_sided(up_snap) and _has_two_sided(dn_snap):
        log.info("live_orderbook: PASS both sides have liquidity on both tokens")
        return 0
    log.warning("live_orderbook: one or both sides missing liquidity at end of run")
    return 1
