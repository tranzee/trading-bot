"""Live BTC ticker — implements `polybot ticker`.

Phase 2 acceptance: runs for N minutes, prints two confirmed 5-min candles
with correct OHLCV, divergence stays under 5 bps. Exits 0 on success, 1 if
divergence trips, 2 if a source disconnects without recovery.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

from rich.console import Console
from rich.live import Live
from rich.table import Table

from config import constants as K
from config.settings import Settings
from polybot.obs.logger import log
from polybot.truth.binance_ws import BinanceWs
from polybot.truth.candle_aggregator import CandleAggregator
from polybot.truth.chainlink_rtds import ChainlinkRtds
from polybot.truth.coinbase_ws import CoinbaseWs
from polybot.truth.models import BtcSource, BtcTick, Candle
from polybot.truth.ticker_tracker import TickerTracker


def _build_table(
    *,
    tracker: TickerTracker,
    agg: CandleAggregator,
    started_ms: int,
    confirmed_so_far: int,
    max_div_bps: Decimal,
) -> Table:
    table = Table(title="polybot ticker (Phase 2)")
    table.add_column("source")
    table.add_column("price", justify="right")
    table.add_column("age (s)", justify="right")

    wall_now_ms = int(time.time() * 1000)
    for src in (BtcSource.BINANCE, BtcSource.COINBASE, BtcSource.CHAINLINK):
        tick = tracker.last_per_source.get(src)
        if tick is None:
            table.add_row(src.value, "-", "-")
        else:
            age_s = max(0, (wall_now_ms - tick.ts_ms) / 1000)
            table.add_row(src.value, f"{tick.price}", f"{age_s:.1f}")

    consensus = tracker.get_price()
    div = tracker.divergence_bps()
    elapsed = (wall_now_ms - started_ms) / 1000

    # In-progress candle line
    live = agg.live_candle()
    live_str = (
        f"open={live.open} high={live.high} low={live.low} close={live.close} "
        f"vol={live.volume} ticks={live.n_ticks}"
        if live is not None
        else "(no live candle yet)"
    )
    last = agg.latest_closed()
    last_str = (
        f"OHLCV {last.open}/{last.high}/{last.low}/{last.close}/{last.volume}"
        if last is not None
        else "(none)"
    )
    next_close_s = (
        max(0, ((live.ts_ms + K.SLOT_DURATION_MS) - wall_now_ms) / 1000) if live else 0
    )
    table.caption = (
        f"consensus={consensus} | divergence={div if div is not None else '-'} bps "
        f"(max so far: {max_div_bps:.1f}) | confirmed candles: {confirmed_so_far}\n"
        f"running {elapsed:.0f}s | next close in {next_close_s:.0f}s\n"
        f"live  : {live_str}\n"
        f"last  : {last_str}"
    )
    return table


async def run_live_ticker(
    duration_s: int = 600,
    *,
    target_confirmed: int = 2,
    divergence_pass_bps: Decimal = Decimal("5"),
    settings: Settings | None = None,
    headless: bool | None = None,
) -> int:
    """Run the live ticker for `duration_s` seconds.

    Returns:
        0 — Phase 2 acceptance met (>= target_confirmed candles, max divergence < pass threshold)
        1 — divergence threshold breached
        2 — required CEX source disconnected for too long
        3 — duration ended before target_confirmed candles closed
    """
    if settings is None:
        from config.settings import load_settings

        settings = load_settings()

    sources = settings.btc_sources

    tracker = TickerTracker(
        required_sources=tuple(
            BtcSource(s) for s in sources if s in ("binance", "coinbase")
        ),
        divergence_threshold_bps=Decimal(settings.DIVERGENCE_THRESHOLD_BPS),
        stale_timeout_s=float(settings.SOURCE_STALE_TIMEOUT_S),
    )
    agg = CandleAggregator(primary_source=BtcSource.BINANCE)

    confirmed: list[Candle] = []
    max_div_seen: list[Decimal] = [Decimal(0)]

    def on_tick(tick: BtcTick) -> None:
        tracker.on_tick(tick)
        agg.on_tick(tick)
        d = tracker.divergence_bps()
        if d is not None and d > max_div_seen[0]:
            max_div_seen[0] = d

    def on_candle_close(candle: Candle) -> None:
        confirmed.append(candle)
        log.info(
            "candle: ts={} O={} H={} L={} C={} V={} ticks={}",
            candle.ts_ms,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.n_ticks,
        )

    agg.add_listener(on_candle_close)

    bws = BinanceWs()
    cws = CoinbaseWs() if "coinbase" in sources else None
    rtds = ChainlinkRtds() if "chainlink" in sources else None

    bws.add_tick_handler(on_tick)
    if cws:
        cws.add_tick_handler(on_tick)
    if rtds:
        rtds.add_tick_handler(on_tick)

    await bws.start()
    if cws:
        await cws.start()
    if rtds:
        await rtds.start()

    ready = await bws.wait_ready(timeout_s=30)
    if not ready:
        log.error("ticker: binance failed to connect within 30s")
        await bws.stop()
        if cws:
            await cws.stop()
        if rtds:
            await rtds.stop()
        return 2

    started_ms = int(time.time() * 1000)
    deadline = time.monotonic() + duration_s
    rc = 3

    # Auto-detect headless: if stdout is not a tty, skip Rich Live (which
    # blocks waiting for a terminal that never paints).
    if headless is None:
        import sys as _sys

        headless = not _sys.stdout.isatty()

    try:
        if headless:
            log.info(
                "ticker: headless mode; emitting one status line per 30s and one log per candle close"
            )
            last_status_ms = 0
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                wall_now_ms = int(time.time() * 1000)
                if wall_now_ms - last_status_ms >= 30_000:
                    last_status_ms = wall_now_ms
                    consensus = tracker.get_price()
                    div = tracker.divergence_bps()
                    health = tracker.is_healthy()
                    log.info(
                        f"ticker.status: consensus={consensus} divergence_bps={div} "
                        f"max_div={max_div_seen[0]:.2f} confirmed={len(confirmed)} "
                        f"healthy={health.healthy}({health.reason})"
                    )
                if len(confirmed) >= target_confirmed and max_div_seen[0] < divergence_pass_bps:
                    rc = 0
                    break
        else:
            console = Console()
            with Live(
                _build_table(
                    tracker=tracker, agg=agg, started_ms=started_ms,
                    confirmed_so_far=len(confirmed), max_div_bps=max_div_seen[0],
                ),
                console=console,
                refresh_per_second=2,
            ) as live:
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.5)
                    health = tracker.is_healthy()
                    if not health.healthy:
                        log.warning(f"ticker: unhealthy: {health.reason}")
                    live.update(
                        _build_table(
                            tracker=tracker, agg=agg, started_ms=started_ms,
                            confirmed_so_far=len(confirmed), max_div_bps=max_div_seen[0],
                        )
                    )
                    if len(confirmed) >= target_confirmed and max_div_seen[0] < divergence_pass_bps:
                        rc = 0
                        break
    finally:
        await bws.stop()
        if cws:
            await cws.stop()
        if rtds:
            await rtds.stop()

    if rc == 0:
        log.info(
            "ticker: PASS {} candles confirmed; max divergence {:.2f} bps < {}",
            len(confirmed),
            max_div_seen[0],
            divergence_pass_bps,
        )
    elif max_div_seen[0] >= divergence_pass_bps:
        log.warning(
            "ticker: divergence breached: max {:.2f} bps >= {}",
            max_div_seen[0],
            divergence_pass_bps,
        )
        rc = 1
    elif len(confirmed) < target_confirmed:
        log.warning(
            "ticker: only {}/{} candles confirmed before deadline",
            len(confirmed),
            target_confirmed,
        )
        rc = 3
    return rc
