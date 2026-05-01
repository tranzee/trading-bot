# TRANZE — Polymarket Binary Trading Bot

> **A configurable-window, maker-first, BTC price-action-driven bot for Polymarket binary markets. Built natively for Polymarket CLOB V2 on Polygon.**

---

## ⚠️ Critical Notices

- **Real money is at risk.** Every circuit breaker exists for a reason. Do not bypass them.
- **The Phase 3 gate is enforced in code.** The bot reads `state/phase3_report.json` on startup and refuses live mode if the gate has not passed or the file is absent.
- **Verify your legal access to Polymarket** from your jurisdiction before setting `RUN_MODE=live`.
- **Never commit `.env` or the `state/` directory.** `.gitignore` enforces this — check `git status` before every push.

---

## Table of Contents

1. [What TRANZE Is](#what-tranze-is)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Prerequisites](#prerequisites)
5. [Installation](#installation)
6. [Wallet Setup & Linking](#wallet-setup--linking)
7. [Configuration](#configuration)
   - [Environment Variables (`.env`)](#environment-variables-env)
   - [Bot Configurations (`config/bot_configuration.py`)](#bot-configurations-configbot_configurationpy)
   - [Strategy Parameters (`config/strategy_params.yaml`)](#strategy-parameters-configstrategy_paramsyaml)
   - [Economic Calendar (`config/calendar.yaml`)](#economic-calendar-configcalendaryaml)
8. [Running the Bot](#running-the-bot)
9. [CLI Reference](#cli-reference)
10. [Understanding the Signal Engine](#understanding-the-signal-engine)
11. [Risk Management & Circuit Breakers](#risk-management--circuit-breakers)
12. [Monitoring & Observability](#monitoring--observability)
13. [The Phase 3 Statistical Gate](#the-phase-3-statistical-gate)
14. [Development & Testing](#development--testing)

---

## What TRANZE Is

TRANZE is a production-grade Polymarket binary market trading bot driven by a BTC price action engine. It:

- **Reads live BTC price data** from three independent sources — Binance WebSocket, Coinbase Advanced Trade WebSocket, and Polymarket's Chainlink RTDS feed — aggregating them into a divergence-checked consensus price.
- **Runs a full price action analysis engine** on closed BTC candles: 4-tier liquidity hierarchy (MAIN / SLQ / TLQ / ILQ), EPA/IPA efficiency tracking, and Malaysian Supply & Demand zone detection.
- **Fires Alert 3 signals** only on confirmed structural rejections, then places **post-only (maker) orders** on the corresponding Polymarket binary market.
- **Is configurable by market window.** The slot duration is set per named configuration (e.g., `btc_5m`) in `config/bot_configuration.py` — it is not hardcoded to any single timeframe.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          CONTROL PLANE                                 │
│          CLI  →  startup  →  SlotLoop  →  SlotPosition(s)             │
└────────────────────────────────────────────────────────────────────────┘
         │                         │                          │
         ▼                         ▼                          ▼
┌──────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│   TRUTH LAYER    │   │   SIGNAL LAYER      │   │   EXECUTION LAYER   │
│                  │   │                     │   │                     │
│  binance_ws      │tick│  candle_aggregator  │sig │  market_discovery   │
│  coinbase_ws  ───┼───▶  signal/engine      ├───▶  orderbook          │
│  chainlink_rtds  │   │  (pivots, liquidity,│   │  order_dsl          │
│  ticker_tracker  │   │   snd_zones, hunt,  │   │  position           │
└──────────────────┘   │   htf_filter,       │   └─────────────────────┘
                       │   event_filter,     │
                       │   invalidation)     │
                       └─────────────────────┘
                                  │
         ┌────────────────────────┼──────────────────────────┐
         ▼                        ▼                          ▼
┌─────────────────┐   ┌───────────────────────┐   ┌──────────────────┐
│   RISK LAYER    │   │   PERSISTENCE/OBS     │   │   RETRY/CB       │
│  guard          │   │  recorder (NDJSON)    │   │  retry (OPEN /   │
│  sizer          │   │  chart_generator      │   │  HALF_OPEN /     │
│  gate_validator │   │  (HTML charts)        │   │  CLOSED)         │
└─────────────────┘   └───────────────────────┘   └──────────────────┘
```

---

## Project Structure

```
TRANZE/
├── src/
│   └── polybot/
│       ├── cli.py                 # Entry point — all CLI subcommands
│       ├── startup.py             # Startup banner and pre-flight checks
│       ├── truth/
│       │   ├── binance_ws.py      # Binance kline + trade WebSocket
│       │   ├── coinbase_ws.py     # Coinbase Advanced Trade WebSocket (BTC-USD)
│       │   ├── chainlink_rtds.py  # Polymarket RTDS Chainlink BTC feed
│       │   ├── ticker_tracker.py  # Multi-source aggregation & divergence guard
│       │   └── candle_aggregator.py  # Builds closed OHLCV candles
│       ├── signal/
│       │   ├── models.py          # All domain types (Pivot, LiquidityNode, SnDZone, Signal…)
│       │   ├── engine.py          # Orchestrates the full PA engine; produces Signals
│       │   ├── pivots.py          # Swing high/low detection (tentative + confirmed)
│       │   ├── liquidity.py       # 4-tier hierarchy (MAIN / SLQ / TLQ / ILQ)
│       │   ├── snd_zones.py       # Malaysian SnD zone detection (Tier-A patterns)
│       │   ├── hunt.py            # Liquidity sweep / break detection (above / below)
│       │   ├── htf_filter.py      # Higher-timeframe trend filter (UP / DOWN / NEUTRAL)
│       │   ├── event_filter.py    # High-impact macro event blackout
│       │   ├── invalidation.py    # Zone and signal invalidation logic
│       │   └── math.py            # Core signal math utilities
│       ├── poly/
│       │   ├── market_discovery.py  # Finds the target market via Gamma API (by slug)
│       │   ├── orderbook.py         # Live order book WebSocket tracker
│       │   └── order_dsl.py         # Order struct, sides, types, and state machine
│       ├── engine/
│       │   └── position.py          # Per-slot position lifecycle
│       ├── risk/
│       │   ├── guard.py             # Circuit breakers — enforces all entry rules
│       │   ├── sizer.py             # Quarter-Kelly position sizing
│       │   └── gate_validator.py    # Reads and validates phase3_report.json
│       └── obs/
│           ├── recorder.py          # Writes per-slot NDJSON logs (SlotPosition events)
│           ├── chart_generator.py   # Produces interactive HTML trade charts
│           └── retry.py             # Async retry with circuit breaker (OPEN/HALF_OPEN/CLOSED)
├── config/
│   ├── constants.py          # Locked-in platform facts (chain ID, fee rates, pUSD, V2 fields)
│   ├── settings.py           # Pydantic Settings — reads .env and validates all values
│   ├── bot_configuration.py  # Named bot configurations (window_seconds, htf_window_seconds)
│   ├── strategy_params.yaml  # Per-strategy tuning (pivots, zones, execution, sizing)
│   └── calendar.yaml         # High-impact macro event schedule (USD events)
├── tests/
│   ├── unit/                 # One file per module; hand-crafted candle fixtures
│   └── property/             # Hypothesis property-based invariant tests
├── state/                    # gitignored — runtime state
│   ├── pa_engine.json        # PA engine state snapshot
│   ├── guard_state.json      # Risk guard state
│   └── phase3_report.json    # Phase 3 backtest gate result (required for live mode)
├── logs/                     # gitignored — NDJSON trade logs + chart data
├── conftest.py               # Adds src/ to sys.path for all tests
├── pyproject.toml
└── constraints.txt
```

---

## Prerequisites

**Python:** 3.11 or 3.12 (both tested in CI; 3.10 and below are not supported).

**Accounts required:**

- A **Polygon wallet** (any EIP-712-compatible wallet — MetaMask works) funded with **pUSD** as your trading bankroll. pUSD is Polymarket's V2 collateral token: a standard ERC-20 on Polygon backed 1:1 by USDC. The bot checks your pUSD balance, not USDC or USDC.e.
- A **Polymarket account** with CLOB API access. Your CLOB API credentials are derived from your wallet private key — no separate API registration is needed.
- A **Polygon RPC endpoint.** The free public endpoint (`https://polygon-rpc.com`) works; Alchemy or Infura is preferred for production stability.

**Platform note:** Polymarket runs on **CLOB V2** (live since April 28 2026). This codebase targets V2 exclusively. The V1 SDK package (`py-clob-client`, without the `-v2` suffix) is dead and must not be installed.

---

## Installation

### 1. Clone

```bash
git clone <your-repo-url> TRANZE
cd TRANZE
```

### 2. Create a virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 3. Install with pinned constraints

```bash
pip install -e ".[dev]" -c constraints.txt
```

`constraints.txt` pins the full transitive dependency graph, including `py-clob-client-v2` and its Ethereum stack. Always install with `-c constraints.txt` — an unpinned install may pull in incompatible versions of the V2 SDK.

### 4. Verify

```bash
polybot version
polybot --help
```

Both commands should succeed without errors. If `polybot` is not found, confirm your virtual environment is active.

---

## Wallet Setup & Linking

### Step 1 — Copy and fill in `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

```dotenv
POLYMARKET_PRIVATE_KEY=0x<your-64-hex-char-private-key>
POLYMARKET_FUNDER_ADDRESS=0x<your-proxy-funder-address>
POLYMARKET_HOST=https://clob.polymarket.com
POLYGON_RPC_URL=https://polygon-rpc.com
```

`POLYMARKET_FUNDER_ADDRESS` is the proxy/funder address shown in your Polymarket account under **Settings → Funding**. It is a separate address from your personal wallet address.

> Your private key is validated against `^0x[a-fA-F0-9]{64}$` on startup. An incorrectly formatted key causes an immediate, clear exit error before any network connection is attempted.

### Step 2 — Run setup

```bash
polybot setup
```

The interactive setup command validates your credentials, checks connectivity to the Polymarket CLOB and the Polygon RPC, and prints your current pUSD balance. It does not modify any files.

### Step 3 — Confirm system health

```bash
polybot health
```

Checks: settings validity, RiskGuard initialization, and SlotLoop readiness. In live mode it also validates `state/phase3_report.json`. All subsystems must print `[OK]` before you proceed.

---

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POLYMARKET_PRIVATE_KEY` | ✅ | — | Polygon wallet private key (`0x` + 64 hex chars) |
| `POLYMARKET_FUNDER_ADDRESS` | ✅ | — | Proxy/funder address from Polymarket Settings |
| `POLYMARKET_HOST` | ✅ | — | CLOB V2 base URL (`https://clob.polymarket.com`) |
| `POLYGON_RPC_URL` | ✅ | — | Polygon RPC endpoint |
| `RUN_MODE` | | `paper` | `paper` \| `live` \| `backtest` |
| `LOG_LEVEL` | | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `BTC_SOURCES` | | `binance,coinbase,chainlink` | Comma-separated active price sources |
| `WALLET_BALANCE` | | `100.0` | Paper-mode starting bankroll in USD |
| `MAX_SESSION_LOSS` | | `10.0` | USD cumulative loss kill-switch for the session |
| `MAX_DAILY_LOSS` | | `20.0` | USD daily loss ceiling |
| `MAX_PER_TRADE_USD` | | `5.0` | Hard per-trade cap in USD |
| `MAX_PER_TRADE_FRACTION` | | `0.05` | Hard per-trade cap as fraction of bankroll (5%) |
| `STRATEGY` | | `price_action_maker` | Strategy name |
| `MIN_SIGNAL_CONFIDENCE` | | `0.55` | Signals below this threshold are ignored |
| `FORCE_LIVE` | | `false` | Bypass the interactive go-live confirmation prompt |
| `POLYBOT_NO_BANNER` | | unset | Set to any value to suppress the startup banner |

### Bot Configurations (`config/bot_configuration.py`)

This file defines one or more **named bot configurations**. Each configuration specifies the binary market window the bot will trade and the higher-timeframe filter window used by the signal engine.

Each configuration entry has:

| Field | Description |
|-------|-------------|
| `name` | Identifier used with the `--config` flag (e.g., `btc_5m`) |
| `window_seconds` | Slot duration in seconds — the size of the Polymarket binary market window |
| `htf_window_seconds` | Higher-timeframe filter window in seconds (e.g., `3600` for 1-hour) |

The default shipped configuration is `btc_5m`. To trade a different window size, add a new entry to the `configurations` list with the appropriate `window_seconds` and `htf_window_seconds`, then pass its `name` to `--config` when running the bot.

To list all available configurations:

```bash
polybot list configs
```

### Strategy Parameters (`config/strategy_params.yaml`)

Controls fine-grained trading behaviour. Key sections:

| Section | What it controls |
|---------|-----------------|
| `pivots` | Swing high/low lookback; tentative-vs-confirmed pivot handling |
| `snd_zones.enabled_patterns` | Active Tier-A patterns: `dbd`, `rbd`, `inside_bar`, `doji`, `snd_gap` |
| `snd_zones.disabled_patterns_pending_validation` | Tier-B patterns (disabled): `apex`, `a_shape`, `left_shoulder`, `sbr` |
| `snd_zones.freshness_half_life_minutes` | Zone aging half-life: 30 min; zones older than 120 min are discarded |
| `htf_filter` | Higher-timeframe EMA trend filter; signals against trend get a 0.5 confidence multiplier |
| `event_filter` | Blocks entries 5 min before / 10 min after high-impact USD macro events |
| `signal_confidence.min_signal_confidence` | Absolute floor (0.55) — no entry placed below this |
| `execution.sizing` | Quarter-Kelly position sizing (0.25 fraction), hard-capped at 5% of bankroll |

> Do not change these values without re-running the full backtester. Tuning parameters mid-run is overfitting to recent trades.

### Economic Calendar (`config/calendar.yaml`)

Lists high-impact USD macro events (CPI, NFP, FOMC, etc.) with their scheduled UTC times. `signal/event_filter.py` reads this file on startup and enforces a blackout window around each event. Update this file at the start of each month with the upcoming release schedule.

---

## Running the Bot

### Paper mode (start here)

Paper mode connects to the real Polymarket order book WebSocket and simulates fills against live prices with virtual money. Logs and charts are identical to live mode.

```bash
polybot run --strategy price_action_maker --config btc_5m
```

`RUN_MODE=paper` is the default. No `--live` flag means paper mode.

### Simulation mode

Runs a fixed number of paper rounds end-to-end without waiting for wall-clock slot boundaries — useful for rapid wiring validation:

```bash
polybot simulate --rounds 50
```

### Backtesting

```bash
polybot backtest --days 28 --out backtest_output/
```

Replays the PA engine over N days of historical BTC candle data, simulates execution, and writes:
- `backtest_output/phase3_report.json` — Phase 3 gate result (copy to `state/` before running live)
- `backtest_output/chart.html` — Interactive HTML chart with all signals overlaid on price

To include a custom bootstrap resample window:

```bash
polybot backtest --days 60 --bootstrap-days 30 --out backtest_output/
```

### PA engine replay

Replay the PA engine over historical candles and dump the raw signal timeline — useful for debugging signal logic without running full execution:

```bash
polybot pa-replay --days 14
```

Output is written to `pa_replay.json`.

### Live trading

Live mode requires `state/phase3_report.json` to be present and passing. `gate_validator.py` enforces this on every startup. Copy the file from your backtest output:

```bash
cp backtest_output/phase3_report.json state/phase3_report.json
```

Then start the bot:

```bash
polybot run --strategy price_action_maker --config btc_5m --live
```

The bot prints active configuration, risk caps, and pUSD balance, then asks for confirmation before placing any real orders (unless `FORCE_LIVE=true` is set in `.env`).

---

## CLI Reference

All subcommands are available via the `polybot` entry point.

```
polybot --help
```

### Commands

| Command | Flags | Description |
|---------|-------|-------------|
| `polybot run` | `--strategy/-s`, `--config/-c`, `--live` | Start the trading engine |
| `polybot simulate` | `--rounds/-n` | Run N paper-trading rounds in fast mode |
| `polybot backtest` | `--days/-d`, `--out/-o`, `--bootstrap-days` | Historical backtest; writes `phase3_report.json` and `chart.html` |
| `polybot pa-replay` | `--days/-d` | PA engine signal-only replay; writes `pa_replay.json` |
| `polybot ticker` | `--duration`, `--target-candles/-t`, `--pass-bps` | Watch live BTC consensus price from all sources |
| `polybot live-orderbook` | `--duration`, `--slot-offset/-s` | Watch the Polymarket order book for an upcoming slot |
| `polybot chart` | | Generate HTML chart from a session NDJSON log |
| `polybot smoke` | | One-slot end-to-end smoke test |
| `polybot health` | | Print subsystem health (`[OK]` / `FAIL`) |
| `polybot setup` | | Interactive credential and connectivity check |
| `polybot list configs` | | List all named configurations in `bot_configuration.py` |
| `polybot version` | | Print the installed package version |

### Command details

**`polybot ticker`** — Verify your BTC data pipeline before running the bot. Subscribes to all configured sources and prints live consensus price and divergence.

```bash
# Run for 90 seconds, expect 1 completed candle, fail if divergence > 50 bps
polybot ticker --duration 90 --target-candles 1 --pass-bps 50

# Run for 12 minutes, expect 2 completed candles
polybot ticker --duration 720 --target-candles 2 --pass-bps 5
```

Exits with code 0 if all targets are met; non-zero if any target is missed.

**`polybot live-orderbook`** — Inspect the Polymarket order book for an upcoming slot without starting the full engine.

```bash
# Watch the next slot's order book for 20 seconds
polybot live-orderbook --duration 20 --slot-offset 1
```

`--slot-offset 1` means "the slot starting one window-length from now."

**`polybot smoke`** — Run this before every deployment or restart after a code change.

```bash
polybot smoke
```

A passing run prints:
```
  [OK] settings
  [OK] RiskGuard
  [OK] SlotLoop
```

A failing run prints `SMOKE FAILED:` followed by the failing subsystem and reason. Do not run the bot in any mode if smoke fails.

**`polybot backtest`** — Use `--days` to control how much historical data is pulled. A minimum of 28 days is needed for a meaningful sample; 60 days is recommended for the Phase 3 gate. `--bootstrap-days` sets the rolling window for the bootstrap confidence interval (defaults to the same as `--days` if not specified).

---

## Understanding the Signal Engine

### Data flow: tick → candle → signal → order

**1. Tick ingestion.** `binance_ws` streams live BTC trades and klines from `wss://stream.binance.com`. `coinbase_ws` provides a divergence reference from Coinbase Advanced Trade. `chainlink_rtds` provides the Polymarket resolution oracle price. `ticker_tracker` aggregates all three sources and exposes a divergence-checked consensus. If any source goes stale or the sources diverge beyond the threshold, the data layer is marked unhealthy and the RiskGuard blocks new entries.

**2. Candle building.** `candle_aggregator` assembles closed OHLCV candles from the trade stream. The in-progress (open) candle is separately accessible for early-warning computations without polluting the closed-candle history.

**3. Structural analysis.** On each candle close, `signal/engine` runs in sequence:

- `pivots.py` — detects swing highs and lows. Two separate pools are maintained: `tentative_pivots` (candidates awaiting two confirming candles) and `confirmed_pivots`. Only confirmed pivots feed the downstream liquidity hierarchy — tentative pivots appear in charts and Alert 2 only.
- `liquidity.py` — builds and updates the 4-tier hierarchy: MAIN (dominant trend extremes), SLQ (secondary confirmed swings), TLQ (tertiary minor swings), ILQ (intra-candle internal pools).
- `hunt.py` — detects liquidity sweeps above highs and below lows, and structural level breaks.
- `snd_zones.py` — identifies Supply and Demand zones from Tier-A mechanical patterns, with exponential freshness decay.

**4. Efficiency tracking.** The engine tracks EPA (Efficient Price Action) conditions C1 and C2, and IPA (Inefficient Price Action) frozen state. These states describe whether the current trend is structurally healthy or has stalled, and affect signal confidence.

**5. Higher-timeframe filter.** `htf_filter.py` maintains a trend direction (UP / DOWN / NEUTRAL) over the `htf_window_seconds` defined in the active bot configuration. Signals that oppose the higher-timeframe trend receive a confidence multiplier of 0.5.

**6. Event filter.** `event_filter.py` reads `config/calendar.yaml` and blocks all new entries during macro event blackout windows. This is a hard gate — not a confidence modifier.

**7. Alert levels.** The engine emits three alert levels:
- **Alert 1** — zone approach detected. No action.
- **Alert 2** — early warning, fired mid-candle. Logged and charted; no order placed.
- **Alert 3** — confirmed rejection on candle close. This is the only alert that triggers an order.

**8. Market discovery and order placement.** On Alert 3, `market_discovery` queries the Polymarket Gamma API by slug to fetch the token IDs for the current slot's binary market. A post-only (maker) BUY order is placed on the UP or DOWN side via `order_dsl`, depending on signal direction.

### Tier-A SnD patterns (active by default)

| Pattern | Code |
|---------|------|
| Drop-Base-Drop | `dbd` |
| Rally-Base-Rally | `rbd` |
| Inside Bar | `inside_bar` |
| Doji | `doji` |
| SnD Gap | `snd_gap` |

### Tier-B patterns (disabled — pending dedicated backtest validation)

`apex`, `a_shape`, `left_shoulder`, `sbr` — stubbed and off by default. Do not enable them without first running a dedicated backtest on each pattern separately.

### Setup type naming convention

Each signal is tagged with a setup type combining the SnD pattern and the liquidity node tier where the rejection occurred. Examples: `DBD-ILQ`, `RBD-SLQ`, `DOJI-ILQ`, `INSIDE-ILQ`, `SND_GAP-ILQ`. These tags appear in all logs, charts, and the Phase 3 report.

---

## Risk Management & Circuit Breakers

`risk/guard.py` is the single gate controlling whether a new entry order may be placed. All conditions below must be true simultaneously. Strategy code cannot bypass the guard.

| Condition | Rule |
|-----------|------|
| Per-trade size | ≤ `MAX_PER_TRADE_FRACTION` of bankroll AND ≤ `MAX_PER_TRADE_USD` |
| Daily loss | Cumulative daily loss has not reached `MAX_DAILY_LOSS` |
| Session loss | Cumulative session loss has not reached `MAX_SESSION_LOSS` |
| Consecutive losses | Fewer than the configured consecutive-loss limit |
| Latency | Median order-reaction time within threshold |
| Data health | `ticker_tracker.is_healthy()` — sources agree within 50 bps, none stale >5 s |
| Balance | pUSD balance ≥ 1.5× planned trade size |
| Phase 3 gate | `gate_validator` confirms `phase3_report.json` passes all thresholds (live mode only) |

Guard state is written to `state/guard_state.json` and restored on restart, so session and daily loss counters survive crashes.

### Retry circuit breaker states

All network calls and order placements go through `obs/retry.py`, which wraps each call in a three-state circuit breaker:

- **CLOSED** — normal operation; requests pass through.
- **OPEN** — too many consecutive failures; requests are rejected immediately without hitting the network. Stays open for a configured cooldown period.
- **HALF_OPEN** — trial state; one probe request is allowed through to test recovery. On success, transitions back to CLOSED; on failure, returns to OPEN.

### Position lifecycle

Each slot position transitions through these states:

```
pending  →  filled  →  settled
   └──────────────→  cancelled
```

`settled` means the binary market has resolved and PnL has been recorded. `cancelled` means the entry order expired unfilled. The `recorder` writes a `SlotPosition` event to the NDJSON log at each transition, with fields: `direction`, `entry_price`, `fill_price`, `gross_pnl_usd`, `net_pnl_usd`, `shares`, `won`.

### Position sizing

`risk/sizer.py` uses **quarter-Kelly** capped at the hard per-trade limits:

```
edge            = signal_confidence × expected_payoff_ratio − 1
kelly_fraction  = edge / variance
raw_size_usd    = bankroll × kelly_fraction × 0.25
size_usd        = min(raw_size_usd, MAX_PER_TRADE_FRACTION × bankroll, MAX_PER_TRADE_USD)
shares          = floor(size_usd / entry_price)
```

Signal confidence is read from `state/phase3_report.json` (the measured continuation probability per setup type). If the file is missing, the sizer has no confidence input and the gate_validator blocks live trading.

---

## Monitoring & Observability

### Session logs

All trade activity is written to `logs/` as NDJSON — one JSON record per event. Each `SlotPosition` record contains:

| Field | Description |
|-------|-------------|
| `market_id` | Polymarket binary market identifier |
| `token_id` | CLOB token ID for the side traded |
| `direction` | UP or DOWN |
| `entry_price` | Order placement price |
| `fill_price` | Actual fill price |
| `gross_pnl_usd` | PnL before fees |
| `net_pnl_usd` | PnL after fees and rebates |
| `shares` | Number of shares traded |
| `slot_end_ms` | Slot expiry timestamp (ms) |
| `ts_utc` | Event timestamp in UTC ISO format |
| `won` | `true` / `false` — market resolution outcome |
| `paper` | `true` if this was a paper-mode trade |

### Trade charts

Generate an interactive HTML chart for any session:

```bash
polybot chart
```

Output is written to `chart.html`. Charts show BTC price, Polymarket UP/DOWN order book curves, liquidity nodes as horizontal lines, SnD zones as shaded rectangles, and order events colour-coded by outcome (green = win, red = loss).

### State files

| File | Contents |
|------|----------|
| `state/pa_engine.json` | Full PA engine state — pivots, liquidity nodes, active zones, current signal |
| `state/guard_state.json` | RiskGuard state — session PnL, consecutive losses, breaker status |
| `state/phase3_report.json` | Phase 3 gate result — required for live mode |

---

## The Phase 3 Statistical Gate

The bot enforces a statistical edge test before allowing live trading. `risk/gate_validator.py` reads `state/phase3_report.json` on every startup in live mode and exits if the required thresholds are not met.

### Generating the gate result

```bash
polybot backtest --days 60 --out backtest_output/
cp backtest_output/phase3_report.json state/phase3_report.json
```

### Required thresholds

| Field in `phase3_report.json` | Required value | What it means |
|-------------------------------|---------------|----------------|
| Aggregate continuation probability | ≥ 0.54 | Fraction of Alert-3 signals where BTC moved in the predicted direction at slot close |
| `bootstrap_ci_5pct` | ≥ 0.51 | 5th percentile of 1000 bootstrap resamples — rules out edges that exist only due to a lucky sample |
| `sample_size` | ≥ 200 | Minimum Alert-3 signal count for a meaningful sample |
| Individual setup types | ≥ 3 types each ≥ 0.52 | Prevents a single lucky pattern from carrying the whole result |

### What to do if the gate fails

A gate failure means the strategy, as currently configured, does not have a measurable edge on the configured `window_seconds`. Options:

- **Increase `--days`** — a larger sample reduces noise. Use at least 60 days.
- **Manual sanity check (Stage 3.A)** — sample 20 signals from `pa_replay.json` and verify the bot's structural reasoning matches your own analysis on a chart. If fewer than ~16/20 look correct, there is an engine bug or a parameter mismatch in `strategy_params.yaml` to fix first.
- **Tune `strategy_params.yaml`** — then re-run the full backtest from scratch. Do not selectively re-run only the gate check after tuning.
- **Try a different `window_seconds`** — add a new named configuration in `bot_configuration.py` and backtest with that window.

---

## Development & Testing

### Running the test suite

```bash
# Full unit and property-based tests
pytest tests/unit tests/property -q

# With line coverage report
pytest tests/unit tests/property -q --cov=src/polybot --cov-report=html
```

Tests require these environment variables (dummy values are fine):

```bash
export POLYMARKET_PRIVATE_KEY=0x1111111111111111111111111111111111111111111111111111111111111111
export POLYMARKET_FUNDER_ADDRESS=0xabababababababababababababababababababab
```

### CI pipeline

Three jobs run on every push to `main` or `dev` and on every pull request to `main`:

1. **lint** — `ruff check` and `ruff format --check` across `src/`, `config/`, and `tests/`.
2. **typecheck** — `mypy --strict --ignore-missing-imports` on `src/polybot/__init__.py`, `src/polybot/cli.py`, and `config/`.
3. **tests** — `pytest tests/unit tests/property -q` on both Python 3.11 and 3.12 (matrix).

All three jobs must pass before merging to `main`.

### Code style

| Tool | Version | Role |
|------|---------|------|
| `ruff` | 0.3.4 | Linting and import sorting |
| `black` | 24.3.0 | Code formatting (line length 100) |
| `mypy` | 1.9.0 | Static type checking (strict mode) |
| `hypothesis` | 6.152.2 | Property-based invariant tests |
| `pytest` | ≥7.4 | Unit and integration tests |
| `pytest-asyncio` | ≥0.23 | Async test support |
| `freezegun` | ≥1.4 | Time-freezing in time-sensitive tests |

### Suppressing the startup banner

Set `POLYBOT_NO_BANNER=1` to skip the ASCII banner when running the CLI in scripts or CI:

```bash
POLYBOT_NO_BANNER=1 polybot health
```

### Key platform constants (`config/constants.py`)

Encoded as named constants — not configurable values:

| Constant | Value |
|----------|-------|
| Polygon chain ID | 137 |
| Collateral token | pUSD |
| Maker fee | 0% |
| Taker fee peak (current crypto markets) | 1.56% at p=0.50 |
| Taker fee peak (March 30 2026+ new markets) | 1.80% at p=0.50 |
| Maker rebate share of taker fee pool | 20% |
| Minimum on-book dwell for rebate eligibility | 3.5 s |
| Minimum fee charged | 0.0001 USDC |
| V2 EIP-712 domain version | `"2"` |
| CLOB V2 order fields | `salt`, `maker`, `signer`, `tokenId`, `makerAmount`, `takerAmount`, `side`, `signatureType`, `timestamp`, `metadata`, `builder`, `signature` |
| Removed V1 fields (must not appear in code) | `nonce`, `feeRateBps`, `taker`, `expiration` |