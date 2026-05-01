"""Phase 4 — 5-minute slot trading loop.

Architecture:
    SlotLoop.run_forever()
      ├── _run_tick_feed()          background: Binance+Coinbase WS → candle_q
      └── _run_main_loop()          consumes candle_q, evaluates signals, enters positions

Paper mode  (paper=True, default):
    Positions are virtual. Orders are logged only; no CLOB calls made.
    Fill is simulated at the maker entry price.

Live mode   (paper=False):
    post_only orders placed via PolyClient; fill confirmed by polling.
    Heartbeat task keeps the server-side dead-man cancel alive.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from config import constants as K
from config.bot_configuration import BotConfiguration, load_configuration
from config.settings import Settings
from polybot.engine.position import PositionStatus, SlotPosition
from polybot.obs.logger import log
from polybot.poly.market_discovery import resolve_next_slot, slot_boundary_ms
from polybot.poly.order_dsl import MarketHandle, OrderRequest, Side
from polybot.engine.strategy_context import StrategyBase
from polybot.engine.strategies.price_action_maker import PriceActionMakerStrategy
from polybot.obs.recorder import PositionRecorder
from polybot.risk.guard import RiskGuard
from polybot.risk.sizer import PositionSizer
from polybot.signal.engine import PriceActionEngine, StrategyParams
from polybot.signal.event_filter import EventFilter
from polybot.signal.models import Signal, SignalDirection
from polybot.truth.binance_ws import BinanceWs
from polybot.truth.candle_aggregator import CandleAggregator
from polybot.truth.coinbase_ws import CoinbaseWs
from polybot.truth.models import BtcSource, BtcTick, Candle
from polybot.truth.ticker_tracker import TickerTracker


# How many seconds before slot end to cancel any unfilled order.
_CANCEL_BEFORE_SLOT_END_S: int = 60

# How often (seconds) to poll for live-order fills.
_FILL_POLL_INTERVAL_S: int = 30

# Staleness threshold for health check.
_HEALTH_STALE_MS: int = 10_000


@dataclass
class SlotLoopConfig:
    position_size_usd: Decimal = Decimal("5")
    maker_price_down: Decimal = Decimal("0.49")
    maker_price_up: Decimal = Decimal("0.51")
    maker_rebate_per_trade_usd: Decimal = Decimal("0.005")
    cancel_before_slot_end_s: int = _CANCEL_BEFORE_SLOT_END_S
    # Slot offset for market discovery (1 = next slot from now)
    slot_offset: int = 1


class SlotLoop:
    """Async 5-minute slot trading loop.

    Usage::

        loop = SlotLoop(settings=settings, poly=poly, engine=engine,
                        event_filter=ef, paper=True)
        await loop.run_forever()
    """

    def __init__(
        self,
        *,
        settings: Settings,
        poly: object,           # PolyClient (typed as object to avoid circular import)
        engine: PriceActionEngine,
        strategy: StrategyBase | None = None,
        event_filter: EventFilter | None = None,
        config: SlotLoopConfig | None = None,
        bot_config: BotConfiguration | None = None,
        paper: bool = True,
    ) -> None:
        self._settings = settings
        self._poly = poly
        self._engine = engine
        self._cfg = config or SlotLoopConfig()
        self._bot_cfg = bot_config or load_configuration("btc_5m")
        self._strategy: StrategyBase = strategy or PriceActionMakerStrategy(
            maker_price_down=self._cfg.maker_price_down,
            maker_price_up=self._cfg.maker_price_up,
        )
        self._event_filter = event_filter
        self._paper = paper

        self._ticker = TickerTracker(stale_timeout_s=_HEALTH_STALE_MS / 1000)
        self._aggregator = CandleAggregator(
            primary_source=BtcSource.BINANCE,
            window_seconds=self._bot_cfg.window_seconds,
        )
        self._candle_q: asyncio.Queue[Candle] = asyncio.Queue(maxsize=32)
        self._sizer = PositionSizer(settings)
        self._guard = RiskGuard(settings)
        self._recorder = PositionRecorder(settings.logs_dir)

        # Running tallies
        self._slot_count: int = 0
        self._signal_count: int = 0
        self._positions: list[SlotPosition] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Start all subsystems. Runs until task is cancelled."""
        # Configuration manifest (so config A can never silently use config B's priors).
        log.info(
            "slot_loop: configuration_manifest config={} asset={} window_s={} priors={}",
            self._bot_cfg.name, self._bot_cfg.asset,
            self._bot_cfg.window_seconds, self._bot_cfg.priors_path,
        )
        log.info(
            "slot_loop: starting ({} mode); position_size={}",
            "PAPER" if self._paper else "LIVE", self._cfg.position_size_usd,
        )
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._run_tick_feed(), name="tick_feed")
            tg.create_task(self._run_main_loop(), name="main_loop")
            if not self._paper:
                tg.create_task(self._run_heartbeat(), name="heartbeat")

    # ------------------------------------------------------------------
    # Background: tick feed → candle aggregator → candle_q
    # ------------------------------------------------------------------

    async def _run_tick_feed(self) -> None:
        """Connect Binance + Coinbase WS; feed ticks to tracker and aggregator."""
        binance = BinanceWs(stream_path=self._bot_cfg.binance_stream_pattern)
        coinbase = CoinbaseWs(product=self._bot_cfg.coinbase_product)

        def _on_tick(tick: BtcTick) -> None:
            self._ticker.update(tick)
            closed = self._aggregator.on_tick(tick)
            if closed is not None:
                try:
                    self._candle_q.put_nowait(closed)
                except asyncio.QueueFull:
                    log.warning("slot_loop: candle_q full; dropping candle ts={}", closed.ts_ms)

        binance.add_tick_handler(_on_tick)
        coinbase.add_tick_handler(_on_tick)

        await asyncio.gather(binance.start(), coinbase.start())

    # ------------------------------------------------------------------
    # Main loop: wait for candle → evaluate → enter
    # ------------------------------------------------------------------

    async def _run_main_loop(self) -> None:
        """Consume closed candles and evaluate signals."""
        while True:
            try:
                candle = await asyncio.wait_for(self._candle_q.get(), timeout=400.0)
            except asyncio.TimeoutError:
                log.warning("slot_loop: no candle in 400s; tick feed may be stalled")
                continue

            self._slot_count += 1
            await self._process_candle_close(candle)

    async def _process_candle_close(self, candle: Candle) -> None:
        now_ms = int(time.time() * 1000)
        log.info(
            "slot_loop: candle close ts={} open={} close={} vol={}",
            candle.ts_ms, candle.open, candle.close, candle.volume,
        )

        # Health gate: ticker divergence / staleness
        health = self._ticker.is_healthy()

        # Economic event filter
        event_blocked = False
        if self._event_filter is not None:
            ts_utc = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
            event_blocked = bool(self._event_filter.is_blocked_at(ts_utc)[0])

        # PA engine signal (synchronous — engine is not thread-safe)
        signal: Signal | None = self._engine.on_candle_close(candle)
        if signal is None:
            log.info("slot_loop: no signal this candle")
            return

        # Guard check (all 7 circuit breakers)
        allowed, reason = self._guard.allow_entry(
            size_usd=self._sizer.compute_size_usd(
                win_prob=signal.continuation_prior,
                entry_price=self._cfg.maker_price_down,
            ),
            signal_confidence=signal.confidence,
            health_ok=health.healthy,
            event_blocked=event_blocked,
        )
        if not allowed:
            log.info("slot_loop: guard blocked — {}", reason)
            return

        self._signal_count += 1
        log.info(
            "slot_loop: signal {} setup={} depth={} confidence={:.4f}",
            signal.direction.value, signal.setup_type.value,
            signal.depth_bucket.value, float(signal.confidence),
        )

        # Resolve market for the slot that just opened
        market = await self._discover_market(candle.ts_ms)
        if market is None:
            log.warning("slot_loop: could not resolve market; skipping signal")
            return

        # Enter position
        pos = await self._enter_position(signal, market, candle)
        if pos is not None:
            self._positions.append(pos)
            log.info(
                "slot_loop: position opened dir={} shares={} entry={} paper={} order={}",
                pos.direction, pos.shares, pos.entry_price, pos.paper, pos.order_id,
            )
            # Schedule cancellation / settlement monitor
            asyncio.create_task(
                self._monitor_position(pos, market_id=market.condition_id),
                name=f"monitor_{pos.slot_end_ms}",
            )

    # ------------------------------------------------------------------
    # Market discovery
    # ------------------------------------------------------------------

    async def _discover_market(self, signal_ts_ms: int) -> MarketHandle | None:
        """Resolve the market for the slot that starts at signal_ts_ms."""
        try:
            market = await resolve_next_slot(
                self._poly,
                slot_offset=0,        # offset=0 means current active slot
                now_ms=signal_ts_ms,
                window_ms=self._bot_cfg.window_ms,
                slug_pattern=self._bot_cfg.polymarket_slug_pattern,
            )
            return market
        except Exception as exc:
            log.warning("slot_loop: market discovery failed: {}", exc)
            return None

    # ------------------------------------------------------------------
    # Position entry
    # ------------------------------------------------------------------

    async def _enter_position(
        self,
        signal: Signal,
        market: MarketHandle,
        candle: Candle,
    ) -> SlotPosition | None:
        """Delegate order decision to strategy; place or simulate the order."""
        # Kelly sizing — uses continuation_prior as win probability
        # Use DOWN price as proxy for sizing; strategy picks the exact price
        size_usd = self._sizer.compute_size_usd(
            win_prob=signal.continuation_prior,
            entry_price=self._cfg.maker_price_down,
        )
        if size_usd <= 0:
            blocked, reason = self._sizer.is_blocked()
            log.info(
                "slot_loop: position skipped — {}",
                reason if blocked else "Kelly size ≤ 0 (edge below breakeven)",
            )
            return None

        req = self._strategy.decide(signal, market, candle, size_usd=size_usd)
        if req is None:
            log.info("slot_loop: strategy returned None; skipping signal")
            return None

        slot_end_ms = candle.ts_ms + self._bot_cfg.window_ms
        pos = SlotPosition(
            direction=signal.direction.value,
            token_id=req.token_id,
            shares=req.shares,
            entry_price=req.price,
            slot_end_ms=slot_end_ms,
            paper=self._paper,
        )

        if self._paper:
            pos.record_fill(req.price)
            log.info(
                "slot_loop: PAPER fill strategy={} dir={} shares={} @ {} (slot ends {})",
                self._strategy.name, pos.direction, pos.shares, pos.entry_price,
                datetime.fromtimestamp(slot_end_ms / 1000, tz=timezone.utc).isoformat(),
            )
        else:
            try:
                placed = await self._poly.place_order(req, post_only=True)  # type: ignore[attr-defined]
                pos.order_id = placed.order_id
                log.info(
                    "slot_loop: LIVE order placed strategy={} order_id={} dir={} shares={} @ {}",
                    self._strategy.name, pos.order_id, pos.direction, pos.shares, pos.entry_price,
                )
            except Exception as exc:
                log.error("slot_loop: order placement failed: {}", exc)
                return None

        return pos

    # ------------------------------------------------------------------
    # Position monitor: cancel + settlement
    # ------------------------------------------------------------------

    async def _monitor_position(self, pos: SlotPosition, market_id: str = "") -> None:
        """Poll for fill; cancel unfilled at T-60s; record settlement outcome."""
        now_ms = int(time.time() * 1000)
        cancel_at_ms = pos.slot_end_ms - (self._cfg.cancel_before_slot_end_s * 1000)
        settle_at_ms = pos.slot_end_ms + 5_000   # 5s grace after settlement

        # --- Fill monitor (live only): poll every 30s until fill or cancel window ---
        if not self._paper and pos.order_id:
            while pos.status is PositionStatus.PENDING:
                now_ms = int(time.time() * 1000)
                if now_ms >= cancel_at_ms:
                    break
                sleep_s = min(_FILL_POLL_INTERVAL_S, max(0.0, (cancel_at_ms - now_ms) / 1000))
                await asyncio.sleep(sleep_s)
                try:
                    open_orders = await self._poly.get_open_orders()  # type: ignore[attr-defined]
                    open_ids = {o.order_id for o in open_orders}
                    if pos.order_id not in open_ids:
                        # No longer open → assume filled at maker price
                        pos.record_fill(pos.entry_price)
                        log.info("slot_loop: fill detected order_id={}", pos.order_id)
                        break
                except Exception as exc:
                    log.warning("slot_loop: fill poll failed: {}", exc)
        else:
            # Paper or no order_id: just wait until cancel window
            wait_cancel_s = max(0.0, (cancel_at_ms - now_ms) / 1000)
            await asyncio.sleep(wait_cancel_s)

        # --- Cancel unfilled live order ---
        if not self._paper and pos.status is PositionStatus.PENDING and pos.order_id:
            try:
                await self._poly.cancel_order(pos.order_id)  # type: ignore[attr-defined]
                pos.cancel()
                log.info("slot_loop: cancelled unfilled order {}", pos.order_id)
                return
            except Exception as exc:
                log.warning("slot_loop: cancel failed for {}: {}", pos.order_id, exc)

        # --- Wait for settlement ---
        now_ms = int(time.time() * 1000)
        wait_settle_s = max(0.0, (settle_at_ms - now_ms) / 1000)
        await asyncio.sleep(wait_settle_s)

        if pos.status is PositionStatus.PENDING and self._paper:
            pos.record_fill(pos.entry_price)

        if pos.status is PositionStatus.FILLED:
            won = await self._determine_outcome(pos)
            if won is not None:
                rebate = self._cfg.maker_rebate_per_trade_usd
                pos.record_settlement(won=won, maker_rebate_usd=rebate)
                self._sizer.record_outcome(won=won, pnl_usd=pos.net_pnl_usd)
                self._guard.record_outcome(won=won, pnl_usd=pos.net_pnl_usd)
                self._recorder.record_settlement(pos, market_id=market_id)
                log.info(
                    "slot_loop: settlement dir={} won={} gross={} net={}",
                    pos.direction, won, pos.gross_pnl_usd, pos.net_pnl_usd,
                )

    async def _determine_outcome(self, pos: SlotPosition) -> bool | None:
        """Return True if the position won, False if lost, None if unknown.

        Paper mode: compare BTC price at settlement to price at entry.
        Live mode: check token final price via order book or market resolution.
        """
        if self._paper:
            current_price = self._ticker.get_price()
            if current_price is None:
                return None
            entry_btc = self._aggregator.live_candle()
            if entry_btc is None:
                return None
            # DOWN wins if BTC fell since the slot opened
            if pos.direction == "DOWN":
                return current_price < entry_btc.open
            else:
                return current_price > entry_btc.open
        # Live: attempt to read resolved price from order book
        try:
            snap = await self._poly.get_order_book(pos.token_id)  # type: ignore[attr-defined]
            mid = snap.mid()
            if mid is not None:
                return mid > Decimal("0.95")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Heartbeat (live mode only)
    # ------------------------------------------------------------------

    async def _run_heartbeat(self) -> None:
        """Keep the CLOB dead-man cancel alive."""
        while True:
            try:
                await self._poly.post_heartbeat()  # type: ignore[attr-defined]
            except Exception as exc:
                log.warning("slot_loop: heartbeat failed: {}", exc)
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, object]:
        settled = [p for p in self._positions if p.status is PositionStatus.SETTLED]
        wins = [p for p in settled if p.settled_win is True]
        total_net = sum((p.net_pnl_usd for p in settled), Decimal("0"))
        return {
            "slots_processed": self._slot_count,
            "signals_emitted": self._signal_count,
            "positions_opened": len(self._positions),
            "positions_settled": len(settled),
            "win_count": len(wins),
            "net_pnl_usd": str(total_net),
        }
