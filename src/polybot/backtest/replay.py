"""Phase-3 minimal backtester / replayer.

Drives the PriceActionEngine over a historical 5-min candle sequence,
collects every emitted Signal, and computes:

Stage 3.B (raw directional edge):
    For each signal, compare the close at signal_ts (i.e. candle N's close)
    to the close at signal_ts + 300_000 (i.e. candle N+1's close). A
    'continuation' is when candle N+1's close is on the signaled side
    (DOWN -> N+1 close < N close; UP -> N+1 close > N close).

    Aggregate continuation probability and per-(setup, depth) breakdown.
    Bootstrap 1000 resamples for the 5%-percentile CI.

Stage 3.C (cost-adjusted EV):
    Apply a maker-fill model: assume entry at p=0.49 (DOWN) or 0.51 (UP)
    and exit at the slot's settlement price (1.00 if won, 0.00 if lost),
    with maker rebates priced in.

Outputs:
    state/continuation_priors.json — per-(setup, depth) priors w/ metadata
    backtest_output/phase3_report.json — full result
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean

from polybot.obs.logger import log
from polybot.signal.engine import PriceActionEngine, StrategyParams
from polybot.signal.htf_filter import HtfFilter as _HtfFilter
from polybot.signal.models import Signal, SignalDirection
from polybot.truth.models import BtcSource, BtcTick, Candle


def _synth_ticks_for_candle(c: Candle, n_ticks: int = 6) -> list[BtcTick]:
    """Synthesize monotonic open->close ticks for backtest mode.

    Live operation feeds real CEX ticks into PriceActionEngine.on_tick().
    The backtester only has OHLCV; we synthesize a monotonic linear
    interpolation from open to close so the continuation filter's
    tick-slope agreement check sees the candle's NET direction (which is
    all OHLC genuinely tells us — anything more granular is fabrication).
    """
    if n_ticks < 2:
        n_ticks = 2
    delta = c.close - c.open
    out: list[BtcTick] = []
    for j in range(n_ticks):
        frac = Decimal(j) / Decimal(n_ticks - 1)
        price = c.open + delta * frac
        ts = c.ts_ms + int(295_000 * (j / (n_ticks - 1)))
        out.append(BtcTick(source=BtcSource.BINANCE, ts_ms=ts, price=price, volume=Decimal(0)))
    return out


@dataclass
class SignalEvent:
    """One emitted signal + its measured continuation outcome."""

    direction: str
    setup_type: str
    depth_bucket: str
    confidence: float
    continuation_prior_at_emit: float
    rejection_depth_bps: float
    timestamp_ms: int
    candle_close: float
    next_candle_close: float | None
    continuation: bool | None    # None if no next candle available
    htf_alignment_factor: float = 1.0  # 1.0=aligned/neutral, 0.5=against-trend (§1.5.6)


@dataclass
class CellStats:
    setup_type: str
    depth_bucket: str
    n: int
    continuation_count: int
    @property
    def probability(self) -> float:
        return self.continuation_count / self.n if self.n > 0 else 0.0


@dataclass
class BacktestReport:
    window_start_ms: int
    window_end_ms: int
    n_candles: int
    n_signals: int
    aggregate_continuation: float
    bootstrap_ci_5pct: float
    bootstrap_ci_95pct: float
    cells: dict[str, CellStats]
    setups_clearing_52: int
    expected_value_per_signal_usd: float
    avg_winning_payout_usd: float
    avg_losing_payout_usd: float
    gate_3b_passed: bool
    gate_3c_passed: bool
    provisional: bool
    # HTF-conditional sub-population (signals where htf_alignment_factor >= 1.0)
    htf_aligned_n: int = 0
    htf_aligned_continuation: float = 0.0
    htf_aligned_ci_5pct: float = 0.0
    htf_aligned_ci_95pct: float = 0.0
    gate_3b_htf_passed: bool = False   # relaxed: n>=100, cont>=0.54, ci5>=0.51


def replay(
    candles: Sequence[Candle],
    *,
    bootstrap_iters: int = 1000,
    rng_seed: int = 42,
    cold_start_lookback: int = 200,
    position_size_usd: Decimal = Decimal("5"),
    maker_entry_price_down: Decimal = Decimal("0.49"),
    maker_entry_price_up: Decimal = Decimal("0.51"),
    maker_rebate_per_trade_usd: Decimal = Decimal("0.005"),
    htf_window_ms: int = 3_600_000,
) -> tuple[BacktestReport, list[SignalEvent]]:
    """Run the engine on the candle sequence, return aggregate stats."""
    if len(candles) < cold_start_lookback + 10:
        raise ValueError(
            f"need at least {cold_start_lookback + 10} candles; got {len(candles)}"
        )

    # Cold-start on the first lookback candles
    def _new_engine() -> PriceActionEngine:
        return PriceActionEngine(
            StrategyParams(
                cold_start_lookback=cold_start_lookback,
                fail_open_on_missing_priors=True,        # use 0.5 prior to compute candidates
                min_signal_confidence=Decimal("0.0"),    # gate happens AFTER backtest
                htf_enabled=True,                        # NEUTRAL when <50 hourly candles; 1.0× multiplier
            )
        )

    engine = _new_engine()
    engine.bootstrap_from_history(list(candles[:cold_start_lookback]))

    # HTF filter with the full replay history — bypasses the per-reboot window limitation.
    # Each signal's htf_alignment_factor is computed from all candles up to signal time,
    # giving the 1H EMA50 a chance to stabilize even when the PA engine has just rebooted.
    _htf_full = _HtfFilter(period=50, htf_window_ms=htf_window_ms)

    events: list[SignalEvent] = []
    rebootstrap_count = 0
    last_rebootstrap_idx = 0

    # Stream remaining candles one by one
    rest = candles[cold_start_lookback:]
    all_candles = list(candles)
    n = len(rest)
    last_log = 0
    for i, c in enumerate(rest):
        # Feed synthesized ticks before close so the continuation filter has
        # a non-empty tick window to evaluate.
        for tk in _synth_ticks_for_candle(c):
            engine.on_tick(tk)
        sig = engine.on_candle_close(c)
        # Auto re-bootstrap on macro reversal (Phase 3 backtest behavior).
        # Live engines will get this from the operator / restart flow; in a
        # deterministic backtest we reset using the most recent lookback.
        latest_inv = engine.state.latest_invalidation
        if (
            latest_inv is not None
            and latest_inv.type.value in ("absolute_kill_switch", "macro_cycle_reset", "origin")
            and (cold_start_lookback + i) - last_rebootstrap_idx >= cold_start_lookback // 2
        ):
            tip_idx = cold_start_lookback + i + 1
            window_start = max(0, tip_idx - cold_start_lookback)
            engine = _new_engine()
            engine.bootstrap_from_history(all_candles[window_start:tip_idx])
            rebootstrap_count += 1
            last_rebootstrap_idx = cold_start_lookback + i
            log.info(
                "replay: macro reversal at idx={} -> re-bootstrap from window [{}, {})",
                cold_start_lookback + i, window_start, tip_idx,
            )
            continue
        if sig is not None:
            next_close: Decimal | None = None
            if i + 1 < n:
                next_close = rest[i + 1].close
            cont: bool | None = None
            if next_close is not None:
                if sig.direction is SignalDirection.DOWN:
                    cont = bool(next_close < c.close)
                else:
                    cont = bool(next_close > c.close)
            # Compute HTF alignment from the full candle history up to this point.
            # Using all_candles[:signal_idx] gives the 1H EMA50 a stable baseline
            # even immediately after a PA-engine reboot.
            signal_idx = cold_start_lookback + i + 1
            htf_factor = float(_htf_full.alignment_multiplier(
                all_candles[:signal_idx],
                sig.direction.value,
                against_multiplier=Decimal("0.5"),
            ))
            events.append(SignalEvent(
                direction=sig.direction.value,
                setup_type=sig.setup_type.value,
                depth_bucket=sig.depth_bucket.value,
                confidence=float(sig.confidence),
                continuation_prior_at_emit=float(sig.continuation_prior),
                rejection_depth_bps=float(sig.rejection_depth_bps),
                timestamp_ms=sig.timestamp_ms,
                candle_close=float(c.close),
                next_candle_close=float(next_close) if next_close is not None else None,
                continuation=cont,
                htf_alignment_factor=htf_factor,
            ))
        if i // 500 != last_log:
            last_log = i // 500
            log.info("replay: {}/{} candles processed; {} signals so far", i, n, len(events))

    # Filter to events with measurable continuation
    measurable = [e for e in events if e.continuation is not None]
    aggregate = (
        sum(1 for e in measurable if e.continuation) / len(measurable)
        if measurable else 0.0
    )

    # Bootstrap CI
    rng = random.Random(rng_seed)
    if measurable:
        outcomes = [1 if e.continuation else 0 for e in measurable]
        boot_means: list[float] = []
        for _ in range(bootstrap_iters):
            sample = [outcomes[rng.randrange(len(outcomes))] for _ in range(len(outcomes))]
            boot_means.append(mean(sample))
        boot_means.sort()
        ci5 = boot_means[int(0.05 * len(boot_means))]
        ci95 = boot_means[int(0.95 * len(boot_means))]
    else:
        ci5 = ci95 = 0.0

    # Per-(setup, depth) cells
    cells: dict[str, CellStats] = {}
    for e in measurable:
        key = f"{e.setup_type}|{e.depth_bucket}"
        cell = cells.get(key)
        if cell is None:
            cell = CellStats(setup_type=e.setup_type, depth_bucket=e.depth_bucket, n=0, continuation_count=0)
            cells[key] = cell
        cell.n += 1
        if e.continuation:
            cell.continuation_count += 1

    setups_clearing_52 = sum(1 for c in cells.values() if c.n >= 30 and c.probability >= 0.52)

    # EV (cost-adjusted)
    if measurable:
        wins = [e for e in measurable if e.continuation]
        losses = [e for e in measurable if not e.continuation]
        # Maker fill prices: DOWN bot buys DOWN at 0.49 -> wins pay 1.00, loses 0.00
        avg_win = float(position_size_usd) * (1.0 - 0.49) if wins else 0.0
        avg_loss = float(position_size_usd) * 0.49 if losses else 0.0  # cost basis if loses
        # Simplified: EV per signal in USD
        ev = (
            aggregate * float(position_size_usd) * (1.0 - 0.49)
            - (1 - aggregate) * float(position_size_usd) * 0.49
            + float(maker_rebate_per_trade_usd)
        )
    else:
        avg_win = avg_loss = ev = 0.0

    gate_3b = (
        len(measurable) >= 200
        and aggregate >= 0.54
        and ci5 >= 0.51
        and setups_clearing_52 >= 3
    )
    gate_3c = ev > 0.05

    # HTF-aligned conditional sub-population (§1.5.6).
    # Live trading already filters to this population via min_signal_confidence when
    # htf_enabled=True: against-trend signals get 0.5× multiplier → confidence below threshold.
    htf_aligned = [e for e in measurable if e.htf_alignment_factor >= 0.99]
    htf_aligned_cont = (
        sum(1 for e in htf_aligned if e.continuation) / len(htf_aligned)
        if htf_aligned else 0.0
    )
    if len(htf_aligned) >= 2:
        htf_outcomes = [1 if e.continuation else 0 for e in htf_aligned]
        htf_boot: list[float] = []
        for _ in range(bootstrap_iters):
            sample = [htf_outcomes[rng.randrange(len(htf_outcomes))] for _ in range(len(htf_outcomes))]
            htf_boot.append(mean(sample))
        htf_boot.sort()
        htf_ci5 = htf_boot[int(0.05 * len(htf_boot))]
        htf_ci95 = htf_boot[int(0.95 * len(htf_boot))]
    else:
        htf_ci5 = htf_ci95 = 0.0

    gate_3b_htf = (
        len(htf_aligned) >= 100        # relaxed minimum for a filtered sub-population
        and htf_aligned_cont >= 0.54
        and htf_ci5 >= 0.51
    )

    report = BacktestReport(
        window_start_ms=candles[0].ts_ms,
        window_end_ms=candles[-1].ts_ms,
        n_candles=len(candles),
        n_signals=len(events),
        aggregate_continuation=aggregate,
        bootstrap_ci_5pct=ci5,
        bootstrap_ci_95pct=ci95,
        cells=cells,
        setups_clearing_52=setups_clearing_52,
        expected_value_per_signal_usd=ev,
        avg_winning_payout_usd=avg_win,
        avg_losing_payout_usd=avg_loss,
        gate_3b_passed=gate_3b,
        gate_3c_passed=gate_3c,
        provisional=True,    # Phase 3 minimal run is always provisional (re-run in Phase 8)
        htf_aligned_n=len(htf_aligned),
        htf_aligned_continuation=htf_aligned_cont,
        htf_aligned_ci_5pct=htf_ci5,
        htf_aligned_ci_95pct=htf_ci95,
        gate_3b_htf_passed=gate_3b_htf,
    )
    return report, events


def write_continuation_priors(
    report: BacktestReport,
    *,
    path: Path | str = "state/continuation_priors.json",
    min_signals_per_cell: int = 30,
) -> Path:
    """Write the priors table consumed by Signal.confidence (§6.2.8)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cells_out = []
    for key, cell in report.cells.items():
        if cell.n < min_signals_per_cell:
            continue
        cells_out.append({
            "setup_type": cell.setup_type,
            "depth_bucket": cell.depth_bucket,
            "continuation_probability": cell.probability,
            "n": cell.n,
        })
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start_ms": report.window_start_ms,
        "window_end_ms": report.window_end_ms,
        "sample_size": report.n_signals,
        "aggregate_continuation": report.aggregate_continuation,
        "bootstrap_ci_5pct": report.bootstrap_ci_5pct,
        "provisional": report.provisional,
        "min_signals_per_cell": min_signals_per_cell,
        "cells": cells_out,
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("priors: wrote {} cells to {}", len(cells_out), p)
    return p


def write_report(report: BacktestReport, path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # asdict can't handle nested CellStats dict-of-dataclasses cleanly
    raw = asdict(report)
    raw["cells"] = {k: asdict(v) for k, v in report.cells.items()}
    p.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    return p
