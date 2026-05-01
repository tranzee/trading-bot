"""Typer CLI entrypoint for polybot.

Subcommands match MASTER_BLUEPRINT.md §7 Phase 0 acceptance:
    polybot run        — start the live engine (paper or live mode)
    polybot simulate   — run paper-trading simulator over real WS book
    polybot backtest   — run historical backtester (Phase 3 statistical gate)
    polybot chart      — generate HTML chart from a per-market NDJSON log
    polybot setup      — one-time CLOB API credential derivation
    polybot ticker     — print live BTC ticker with divergence (Phase 2 acceptance)
    polybot live-orderbook — print live Polymarket order book (Phase 1 acceptance)
    polybot smoke      — end-to-end paper smoke test (Phase 10 acceptance)
    polybot health     — health probe (returns non-zero on any unhealthy subsystem)
    polybot version    — print version
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from polybot import __version__

app = typer.Typer(
    name="polybot",
    help="Polymarket BTC 5-minute trading bot (CLOB V2, maker-first).",
    add_completion=False,
    no_args_is_help=True,
)


_CONFIG_OPTION = typer.Option(
    "btc_5m",
    "--config", "-c",
    help="Named bot configuration from config/configurations.yaml.",
)


def _resolve_and_announce_config(name: str):
    """Load a named configuration and log the manifest.

    Logging the resolved priors path on every command makes it impossible to
    silently run config A with priors from config B.
    """
    from config.bot_configuration import load_configuration
    cfg = load_configuration(name)
    typer.echo(
        f"configuration_manifest: name={cfg.name} asset={cfg.asset} "
        f"window_s={cfg.window_seconds} priors={cfg.priors_path}"
    )
    return cfg


def _maybe_print_banner() -> None:
    """Print the host/cutover safety banner unless suppressed.

    Suppressed for `version` (so scripts can parse output) and when
    POLYBOT_NO_BANNER=1 is set in the environment. Failures (e.g. missing
    env vars) silently fall back to a minimal host-only line so `--help`
    on a fresh checkout still works.
    """
    if os.environ.get("POLYBOT_NO_BANNER") == "1":
        return
    try:
        from config.settings import load_settings
        from polybot.startup import print_banner

        settings = load_settings()
        print_banner(settings)
    except Exception:
        # .env not yet populated — don't crash --help. Minimal hint instead.
        host = os.environ.get("POLYMARKET_HOST", "(unset)")
        typer.echo(f"polybot (env not loaded; POLYMARKET_HOST={host})")


# --------------------------------------------------------------------------
# Phase >= 4 commands. Stubbed in Phase 0 to satisfy `polybot --help`.
# --------------------------------------------------------------------------


@app.command("run")
def run(
    config: str = _CONFIG_OPTION,
    strategy: str = typer.Option(
        "price_action_maker",
        "--strategy",
        "-s",
        help="Strategy name from config/strategy_params.yaml.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Switch from paper to LIVE mode. Requires confirmation unless FORCE_LIVE=true.",
    ),
    slot_offset: int = typer.Option(
        1, "--slot-offset", help="How many slots ahead to pre-warm (default 1).", min=0, max=4
    ),
    bootstrap_days: int = typer.Option(
        7, "--bootstrap-days", help="Days of historical candles to bootstrap the PA engine.", min=1,
    ),
) -> None:
    """Start the engine in paper (default) or live mode."""
    _maybe_print_banner()
    import asyncio as _asyncio
    import os as _os

    from config.settings import load_settings
    from polybot.backtest.data_loader import load_recent_days_for_config
    from polybot.engine.slot_loop import SlotLoop, SlotLoopConfig
    from polybot.obs.logger import configure_from_settings, log
    from polybot.poly.client import PolyClient
    from polybot.risk.gate_validator import validate_priors_for_config
    from polybot.signal.engine import PriceActionEngine, StrategyParams
    from polybot.signal.event_filter import EventFilter, EventFilterParams

    bot_cfg = _resolve_and_announce_config(config)
    settings = load_settings()
    configure_from_settings(settings.LOG_LEVEL.value)

    if live and not settings.is_live:
        if _os.environ.get("FORCE_LIVE") != "true":
            typer.echo("ERROR: --live requires POLYBOT_MODE=live in env or FORCE_LIVE=true.")
            raise typer.Exit(code=1)

    # Per-configuration gate: refuse live mode if priors don't pass
    if live:
        gate = validate_priors_for_config(bot_cfg)
        if not gate.passed:
            typer.echo(f"ERROR: live gate FAILED for {bot_cfg.name}: {gate.reason}")
            typer.echo("Run `polybot backtest --config {}` until the gate passes.".format(bot_cfg.name))
            raise typer.Exit(code=1)
        typer.echo(f"live gate PASS for {bot_cfg.name}: {gate.reason}")

    if live and not settings.is_live:
        typer.confirm(
            "WARNING: switching to LIVE mode will place real orders. Continue?",
            abort=True,
        )

    paper = not live
    typer.echo(f"polybot run  config={bot_cfg.name}  mode={'PAPER' if paper else 'LIVE'}  strategy={strategy}")

    # Bootstrap PA engine from recent history (per-config asset/window)
    log.info("run: loading {} days of history for {} bootstrap", bootstrap_days, bot_cfg.name)
    candles = load_recent_days_for_config(bootstrap_days, bot_cfg)
    if not candles:
        typer.echo("No candles available for bootstrap; check network.")
        raise typer.Exit(code=2)

    lookback = min(200, len(candles))
    engine = PriceActionEngine(StrategyParams(cold_start_lookback=lookback))
    engine.bootstrap_from_history(candles[-lookback:])
    log.info("run: engine bootstrapped trend={}", engine.state.trend.value)

    # Event filter — fail_closed=False so a missing calendar doesn't block trading
    event_filter = EventFilter(
        Path("config/calendar.yaml"),
        params=EventFilterParams(fail_closed_on_unhealthy=False),
        register_sighup=True,
    )

    poly = PolyClient(settings)
    cfg = SlotLoopConfig(slot_offset=slot_offset)
    slot_loop = SlotLoop(
        settings=settings,
        poly=poly,
        engine=engine,
        event_filter=event_filter,
        config=cfg,
        bot_config=bot_cfg,
        paper=paper,
    )

    try:
        _asyncio.run(slot_loop.run_forever())
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
    finally:
        stats = slot_loop.stats
        typer.echo(
            f"Stats: slots={stats['slots_processed']} signals={stats['signals_emitted']} "
            f"positions={stats['positions_opened']} pnl={stats['net_pnl_usd']}"
        )
    raise typer.Exit(code=0)


@app.command("simulate")
def simulate(
    config: str = _CONFIG_OPTION,
    rounds: int = typer.Option(50, "--rounds", "-n", help="Number of slots to simulate.", min=1),
    strategy: str = typer.Option("price_action_maker", "--strategy", "-s"),
    bootstrap_days: int = typer.Option(7, "--bootstrap-days", min=1),
) -> None:
    """Run the paper-trading simulator (virtual fills, no real orders placed)."""
    _maybe_print_banner()

    from config.settings import load_settings
    from polybot.backtest.data_loader import load_recent_days_for_config
    from polybot.engine.slot_loop import SlotLoop, SlotLoopConfig
    from polybot.engine.strategies.simulation import SimulationStrategy
    from polybot.engine.strategies.price_action_maker import PriceActionMakerStrategy
    from polybot.obs.logger import configure_from_settings
    from polybot.signal.engine import PriceActionEngine, StrategyParams
    from polybot.sim.paper_client import PaperClient

    bot_cfg = _resolve_and_announce_config(config)
    settings = load_settings()
    configure_from_settings(settings.LOG_LEVEL.value)

    candles = load_recent_days_for_config(bootstrap_days, bot_cfg)
    if not candles:
        typer.echo("No candles for bootstrap; check network.")
        raise typer.Exit(code=2)

    engine = PriceActionEngine(StrategyParams(cold_start_lookback=min(200, len(candles))))
    engine.bootstrap_from_history(candles[-min(200, len(candles)):])

    strat = SimulationStrategy() if strategy == "simulation" else PriceActionMakerStrategy()
    poly = PaperClient()

    cfg = SlotLoopConfig()
    slot_loop = SlotLoop(
        settings=settings, poly=poly, engine=engine,
        strategy=strat, config=cfg, bot_config=bot_cfg, paper=True,
    )

    typer.echo(f"simulate: strategy={strategy} rounds={rounds}")
    # The simulator runs the same slot loop but exits after `rounds` slots.
    # Since we can't inject synthetic candles here, we report ready state.
    typer.echo("Simulator ready — connect to live WS to drive slots (use polybot run --paper for live WS).")
    stats = slot_loop.stats
    typer.echo(f"Stats: {stats}")
    raise typer.Exit(code=0)


@app.command("backtest")
def backtest(
    config: str = _CONFIG_OPTION,
    days: int = typer.Option(60, "--days", "-d", help="Lookback window in days.", min=1),
    strategy: str = typer.Option("price_action_maker", "--strategy", "-s"),
    output_dir: Path = typer.Option(
        Path("backtest_output"), "--out", "-o", help="Where to write reports."
    ),
    write_priors: bool = typer.Option(
        True,
        "--write-priors/--no-write-priors",
        help="Write state/priors_{config}.json for the engine to consume.",
    ),
) -> None:
    """Run the historical backtester (Phase 3 statistical gate) for one configuration."""
    _maybe_print_banner()

    from polybot.backtest.data_loader import load_recent_days_for_config
    from polybot.backtest.replay import replay, write_continuation_priors, write_report
    from polybot.obs.logger import log

    bot_cfg = _resolve_and_announce_config(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("backtest: loading {} days of {} {}s data", days, bot_cfg.asset, bot_cfg.window_seconds)
    candles = load_recent_days_for_config(days, bot_cfg)
    if not candles:
        typer.echo("No candles loaded — Binance Vision may not have published yet.")
        raise typer.Exit(code=2)
    log.info("backtest: replaying engine over {} candles", len(candles))
    report, events = replay(candles, htf_window_ms=bot_cfg.htf_window_ms)

    report_filename = f"phase3_report_{bot_cfg.name}.json"
    report_path = write_report(report, output_dir / report_filename)
    typer.echo(f"Report: {report_path}")
    typer.echo(
        f"Phase 3.B  | n_signals={report.n_signals} "
        f"continuation={report.aggregate_continuation:.4f} "
        f"ci5={report.bootstrap_ci_5pct:.4f} "
        f"setups>=0.52: {report.setups_clearing_52}  -> "
        f"{'PASS' if report.gate_3b_passed else 'FAIL'}"
    )
    typer.echo(
        f"Phase 3.B' | htf_aligned n={report.htf_aligned_n} "
        f"continuation={report.htf_aligned_continuation:.4f} "
        f"ci5={report.htf_aligned_ci_5pct:.4f}  -> "
        f"{'CONDITIONAL PASS' if report.gate_3b_htf_passed else 'FAIL'}"
    )
    typer.echo(
        f"Phase 3.C  | EV/signal=${report.expected_value_per_signal_usd:.4f}  -> "
        f"{'PASS' if report.gate_3c_passed else 'FAIL'}"
    )
    if write_priors:
        priors_path = write_continuation_priors(report, path=bot_cfg.priors_path)
        typer.echo(f"Priors: {priors_path} (provisional={report.provisional})")

    raise typer.Exit(code=0 if (report.gate_3b_passed and report.gate_3c_passed) else 1)


@app.command("chart")
def chart(
    log_file: Path = typer.Argument(..., help="Per-market NDJSON log file."),
    output: Path = typer.Option(
        Path("chart.html"), "--out", "-o", help="Output HTML file path."
    ),
) -> None:
    """Generate an interactive HTML chart from a per-market log file."""
    from polybot.obs.chart_generator import generate_chart

    try:
        out_path = generate_chart(log_file, output)
        typer.echo(f"Chart written to: {out_path}")
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command("setup")
def setup() -> None:
    """One-time CLOB API credential derivation from POLYMARKET_PRIVATE_KEY."""
    _maybe_print_banner()
    import asyncio as _asyncio

    from config.settings import load_settings
    from polybot.obs.logger import configure_from_settings
    from polybot.poly.client import PolyClient

    async def _run() -> int:
        settings = load_settings()
        configure_from_settings(settings.LOG_LEVEL.value)
        poly = PolyClient(settings)
        await poly.setup_creds()
        creds = poly._creds  # noqa: SLF001
        assert creds is not None
        typer.echo(f"api_key        : {creds.api_key}")
        typer.echo(f"api_passphrase : {creds.api_passphrase}")
        typer.echo("(api_secret printed only by scripts/setup_creds.py)")
        return 0

    raise typer.Exit(code=_asyncio.run(_run()))


@app.command("ticker")
def ticker(
    duration: int = typer.Option(
        600, "--duration", "-t", help="How long to run, in seconds.", min=1
    ),
    target_candles: int = typer.Option(
        2,
        "--target-candles",
        help="Acceptance threshold: number of confirmed candles required.",
        min=1,
    ),
    pass_bps: int = typer.Option(
        5,
        "--pass-bps",
        help="Acceptance threshold: max allowed divergence in bps.",
        min=1,
    ),
) -> None:
    """Print live BTC ticker with divergence (Phase 2 acceptance)."""
    _maybe_print_banner()
    import asyncio as _asyncio
    from decimal import Decimal as _Decimal

    from polybot.truth.live_ticker import run_live_ticker

    rc = _asyncio.run(
        run_live_ticker(
            duration_s=duration,
            target_confirmed=target_candles,
            divergence_pass_bps=_Decimal(pass_bps),
        )
    )
    raise typer.Exit(code=rc)


@app.command("live-orderbook")
def live_orderbook(
    duration: int = typer.Option(60, "--duration", "-t", help="How long to run, in seconds.", min=1),
    slot_offset: int = typer.Option(
        0,
        "--slot-offset",
        help="Which slot to view: 0=current, 1=next, etc.",
        min=0,
        max=4,
    ),
) -> None:
    """Print the live Polymarket order book for the next BTC 5m slot (Phase 1 acceptance)."""
    _maybe_print_banner()
    import asyncio as _asyncio

    from polybot.poly.live_view import run_live_orderbook

    rc = _asyncio.run(run_live_orderbook(duration_s=duration, slot_offset=slot_offset))
    raise typer.Exit(code=rc)


@app.command("smoke")
def smoke() -> None:
    """End-to-end paper smoke test — verifies all subsystems can initialise."""
    _maybe_print_banner()
    import asyncio as _asyncio
    from pathlib import Path as _Path

    from decimal import Decimal as _Decimal

    from config.settings import load_settings
    from polybot.backtest.data_loader import load_recent_days
    from polybot.engine.slot_loop import SlotLoop, SlotLoopConfig
    from polybot.engine.strategies.simulation import SimulationStrategy
    from polybot.obs.logger import configure_from_settings
    from polybot.risk.guard import RiskGuard
    from polybot.signal.engine import PriceActionEngine, StrategyParams
    from polybot.sim.paper_client import PaperClient

    Decimal = _Decimal  # local alias for use below
    failures: list[str] = []
    settings = None
    engine = None

    try:
        settings = load_settings()
        configure_from_settings(settings.LOG_LEVEL.value)
        typer.echo("  [OK] settings")
    except Exception as exc:
        failures.append(f"settings: {exc}")

    try:
        candles = load_recent_days(3)
        assert candles, "no candles returned"
        typer.echo(f"  [OK] data_loader ({len(candles)} candles)")
    except Exception as exc:
        failures.append(f"data_loader: {exc}")

    try:
        engine = PriceActionEngine()
        typer.echo("  [OK] PriceActionEngine")
    except Exception as exc:
        failures.append(f"PriceActionEngine: {exc}")

    if settings is not None:
        try:
            guard = RiskGuard(settings, snapshot_path=_Path("state/smoke_guard.json"))
            allowed, _ = guard.allow_entry(Decimal("5"), Decimal("0.60"))
            assert allowed
            typer.echo("  [OK] RiskGuard")
        except Exception as exc:
            failures.append(f"RiskGuard: {exc}")

        try:
            poly = PaperClient()
            loop = SlotLoop(
                settings=settings,
                poly=poly,
                engine=engine or PriceActionEngine(),
                strategy=SimulationStrategy(),
                paper=True,
            )
            typer.echo("  [OK] SlotLoop")
        except Exception as exc:
            failures.append(f"SlotLoop: {exc}")
    else:
        failures.append("RiskGuard: skipped (settings failed)")
        failures.append("SlotLoop: skipped (settings failed)")

    if failures:
        typer.echo("\nSMOKE FAILED:")
        for f in failures:
            typer.echo(f"  FAIL: {f}", err=True)
        raise typer.Exit(code=1)

    typer.echo("\nsmoke: all subsystems healthy")
    raise typer.Exit(code=0)


@app.command("health")
def health() -> None:
    """Health probe. Returns 0 if all subsystems healthy, non-zero with diagnostic."""
    import asyncio as _asyncio

    from config.settings import load_settings
    from polybot.obs.logger import configure_from_settings
    from polybot.poly.client import PolyClient
    from polybot.truth.ticker_tracker import TickerTracker

    settings = load_settings()
    configure_from_settings(settings.LOG_LEVEL.value)

    issues: list[str] = []

    # Check guard state snapshot
    guard_snap = settings.state_dir / "guard_state.json"
    if guard_snap.exists():
        typer.echo(f"  guard_state: {guard_snap} [OK]")
    else:
        typer.echo(f"  guard_state: {guard_snap} [MISSING — will use defaults]")

    # Check continuation priors
    priors = settings.state_dir / "continuation_priors.json"
    if priors.exists():
        typer.echo(f"  continuation_priors: {priors} [OK]")
    else:
        typer.echo(f"  continuation_priors: {priors} [MISSING — engine uses 0.50 fallback]")
        if settings.is_live:
            issues.append("continuation_priors missing; live mode requires backtest run")

    # Check event calendar
    calendar = Path("config/calendar.yaml")
    if calendar.exists():
        typer.echo(f"  event_calendar: {calendar} [OK]")
    else:
        typer.echo(f"  event_calendar: {calendar} [MISSING — event filter disabled]")

    if issues:
        for issue in issues:
            typer.echo(f"UNHEALTHY: {issue}", err=True)
        raise typer.Exit(code=1)
    typer.echo("health: all checks passed")
    raise typer.Exit(code=0)


@app.command("pa-replay")
def pa_replay(
    csv_file: Path = typer.Argument(..., help="CSV of OHLCV: ts_ms,open,high,low,close,volume"),
    out: Path = typer.Option(Path("pa_replay.json"), "--out", "-o"),
) -> None:
    """Feed a CSV of historical candles into the engine; emit JSON timeline."""
    _maybe_print_banner()
    import csv as _csv
    import json as _json
    from decimal import Decimal as _D

    from polybot.signal.engine import PriceActionEngine
    from polybot.truth.models import Candle

    candles: list[Candle] = []
    with csv_file.open("r", encoding="utf-8") as f:
        reader = _csv.reader(f)
        for row in reader:
            if not row or row[0].lstrip("-").isdigit() is False:
                continue
            candles.append(Candle(
                ts_ms=int(row[0]),
                open=_D(row[1]), high=_D(row[2]),
                low=_D(row[3]), close=_D(row[4]),
                volume=_D(row[5]) if len(row) > 5 else _D(0),
                n_ticks=0,
            ))
    typer.echo(f"loaded {len(candles)} candles")
    engine = PriceActionEngine()
    bootstrap_n = min(200, len(candles) // 2)
    engine.bootstrap_from_history(candles[:bootstrap_n])
    timeline: list[dict] = []
    for c in candles[bootstrap_n:]:
        sig = engine.on_candle_close(c)
        if sig is not None:
            timeline.append({
                "ts_ms": sig.timestamp_ms,
                "direction": sig.direction.value,
                "setup_type": sig.setup_type.value,
                "depth_bucket": sig.depth_bucket.value,
                "confidence": float(sig.confidence),
                "rejection_depth_bps": float(sig.rejection_depth_bps),
            })
    out.write_text(_json.dumps({"signals": timeline}, indent=2), encoding="utf-8")
    typer.echo(f"wrote {len(timeline)} signals to {out}")
    raise typer.Exit(code=0)


configs_app = typer.Typer(name="configs", help="Manage and inspect named bot configurations.")
app.add_typer(configs_app, name="configs")


@configs_app.command("list")
def configs_list() -> None:
    """List every named configuration and whether it has passed the live gate."""
    from config.bot_configuration import load_all_configurations
    from polybot.risk.gate_validator import validate_priors_for_config

    cfgs = load_all_configurations()
    if not cfgs:
        typer.echo("No configurations defined.")
        raise typer.Exit(code=0)

    typer.echo(f"{'NAME':<12} {'ASSET':<6} {'WINDOW':<8} {'GATE':<6}  REASON")
    typer.echo("-" * 78)
    for name in sorted(cfgs):
        cfg = cfgs[name]
        gate = validate_priors_for_config(cfg)
        status = "PASS" if gate.passed else "FAIL"
        typer.echo(
            f"{cfg.name:<12} {cfg.asset:<6} {cfg.window_seconds}s    "
            f"{status:<6}  {gate.reason}"
        )
    raise typer.Exit(code=0)


@app.command("version")
def version() -> None:
    """Print the polybot package version."""
    typer.echo(f"polybot {__version__}")
    raise typer.Exit(code=0)


# --------------------------------------------------------------------------
# Module entrypoint
# --------------------------------------------------------------------------


def main() -> None:  # pragma: no cover
    """Console-script target."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
