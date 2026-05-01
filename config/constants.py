"""Locked-in 2026 platform facts.

These are NOT configurable. They are facts about Polymarket CLOB V2 and the
surrounding environment as of April 2026, encoded as named constants per
MASTER_BLUEPRINT.md §2. Changing any value here is changing the bot's
worldview — do that only when the platform itself changes.

Sources:
- MASTER_BLUEPRINT.md §2 (the locked-in constants section)
- Polymarket V2 migration guide
- Polymarket fees & rebates documentation
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

# ============================================================================
# 2.1 — CLOB V2 (live 2026-04-28 ~11:00 UTC)
# ============================================================================

#: Cutover timestamp in milliseconds since epoch — used by the operator
#: pre-flight to decide which host to talk to. (2026-04-28 11:00:00 UTC)
CLOB_V2_CUTOVER_MS: Final[int] = 1_777_374_000_000

#: Production V2 host. Pre-cutover testing uses CLOB_V2_PRECUTOVER_HOST.
CLOB_V2_PRODUCTION_HOST: Final[str] = "https://clob.polymarket.com"

#: Pre-cutover testnet/staging host. Drops at cutover.
CLOB_V2_PRECUTOVER_HOST: Final[str] = "https://clob-v2.polymarket.com"

#: Polygon chain ID (Polymarket settles here).
POLYGON_CHAIN_ID: Final[int] = 137

#: EIP-712 domain version for the V2 Exchange contract.
EIP712_EXCHANGE_DOMAIN_VERSION: Final[str] = "2"

#: EIP-712 domain version for the ClobAuth domain (unchanged at V2).
EIP712_CLOBAUTH_DOMAIN_VERSION: Final[str] = "1"

#: CTF Exchange V2 verifying contract on Polygon mainnet.
CTF_EXCHANGE_V2_ADDRESS: Final[str] = "0xE111180000d2663C0091e4f400237545B87B996B"

#: Negative-Risk CTF Exchange V2 verifying contract on Polygon mainnet.
NEG_RISK_CTF_EXCHANGE_V2_ADDRESS: Final[str] = "0xe2222d279d744050d28e00520010520000310F59"

#: V2 order struct fields (the canonical signed shape). The bot's order
#: builder must produce exactly these fields, and never include the V1-only
#: fields listed in V2_ORDER_REMOVED_FIELDS.
V2_ORDER_FIELDS: Final[tuple[str, ...]] = (
    "salt",
    "maker",
    "signer",
    "tokenId",
    "makerAmount",
    "takerAmount",
    "side",
    "signatureType",
    "timestamp",
    "metadata",
    "builder",
    "signature",
)

#: Fields removed in the V2 order struct. Including any of these in a V2
#: order will be rejected by the exchange.
V2_ORDER_REMOVED_FIELDS: Final[tuple[str, ...]] = (
    "nonce",
    "feeRateBps",
    "taker",
    "expiration",  # tracked off-signature in V2
)

#: All open orders are wiped at cutover. The bot must handle this once on
#: 2026-04-28 and re-place from scratch.
WIPE_OPEN_ORDERS_AT_CUTOVER: Final[bool] = True

# ============================================================================
# 2.2 — Collateral: pUSD (not USDC.e)
# ============================================================================

#: pUSD is the post-cutover collateral token (ERC-20 on Polygon, 1:1 USDC).
PUSD_DECIMALS: Final[int] = 6

#: Symbol used in logging and balance reports.
PUSD_SYMBOL: Final[str] = "pUSD"

#: API-only traders must wrap USDC.e -> pUSD via Collateral Onramp `wrap()`.
USDCE_REQUIRES_WRAP_FOR_API: Final[bool] = True

# ============================================================================
# 2.3 — Fee structure (current crypto, as of March 2026)
# ============================================================================
# Formula: fee = C * p * feeRate * (p * (1 - p)) ** exponent
# - C        = shares
# - p        = price (0..1)
# - feeRate  = market parameter (returned by getClobMarketInfo())
# - exponent = market parameter (returned by getClobMarketInfo())
# Maker rebate is funded from the daily taker fee pool (per-market scoped).
# ALWAYS query fees dynamically via getClobMarketInfo(); these constants are
# the documented schedule used as a fallback / sanity check.

#: Crypto markets fee rate — current schedule.
FEE_RATE_CRYPTO_CURRENT: Final[Decimal] = Decimal("0.25")

#: Crypto markets exponent — current schedule.
FEE_EXPONENT_CRYPTO_CURRENT: Final[int] = 2

#: Peak effective fee at p=0.50 under the current crypto schedule.
PEAK_EFFECTIVE_FEE_CRYPTO_CURRENT: Final[Decimal] = Decimal("0.0156")  # 1.56%

#: Crypto markets fee rate — schedule applied to NEW markets from 2026-03-30.
FEE_RATE_CRYPTO_POST_MARCH_30: Final[Decimal] = Decimal("0.072")

#: Crypto markets exponent — post-March-30 schedule (linear in p*(1-p)).
FEE_EXPONENT_CRYPTO_POST_MARCH_30: Final[int] = 1

#: Peak effective fee at p=0.50 under the post-March-30 crypto schedule.
PEAK_EFFECTIVE_FEE_CRYPTO_POST_MARCH_30: Final[Decimal] = Decimal("0.0180")  # 1.80%

#: Maker rebate fraction (of the fee pool, daily).
MAKER_REBATE_FRACTION: Final[Decimal] = Decimal("0.20")

#: Smallest charged fee precision (4 decimal places, USDC).
FEE_QUANTUM: Final[Decimal] = Decimal("0.0001")

# ============================================================================
# 2.4 — Maker rebates
# ============================================================================

#: Per-market rebate scoping enabled (Feb 11, 2026 onward).
REBATES_PER_MARKET_SCOPED: Final[bool] = True

#: Minimum dwell time on the book to qualify for rebates (some programs).
#: Verify per-market via getClobMarketInfo().
MIN_REBATE_DWELL_S: Final[float] = 3.5

# ============================================================================
# 2.5 — Other platform constraints
# ============================================================================

#: Minimum order size in shares.
MIN_ORDER_SHARES: Final[int] = 5

#: Maximum batch order size (raised from 5 on 2025-08-21).
BATCH_ORDER_LIMIT: Final[int] = 15

#: Post-only order type supported (added 2026-01-06).
POST_ONLY_SUPPORTED: Final[bool] = True

#: Default tick size for 5-min binary markets (verify per-market via
#: getClobMarketInfo().mts — this is a fallback).
DEFAULT_TICK_SIZE: Final[Decimal] = Decimal("0.01")

# ============================================================================
# 2.5b — WebSocket-only data plane
# ============================================================================
# REST polling is structurally too slow post-Feb-2026; the bot uses WS only
# for live data. REST is acceptable only for one-shot setup queries.

#: Polymarket public WS host for order book / trades / RTDS crypto prices.
POLYMARKET_WS_HOST: Final[str] = "wss://ws-live-data.polymarket.com/"

#: Polymarket Gamma API (HTTP) for slug -> condition_id resolution.
POLYMARKET_GAMMA_API: Final[str] = "https://gamma-api.polymarket.com"

#: HeartBeats endpoint exists for connection-loss-triggered cancel-all.
HEARTBEATS_DEADMAN_SUPPORTED: Final[bool] = True

# ============================================================================
# 2.6 — Off-platform BTC data sources
# ============================================================================

#: Primary BTC source (lowest latency, deepest book).
BINANCE_WS_HOST: Final[str] = "wss://stream.binance.com:9443"
BINANCE_WS_STREAMS: Final[str] = "/stream?streams=btcusdt@trade/btcusdt@kline_5m"

#: Secondary BTC source (divergence reference).
COINBASE_WS_HOST: Final[str] = "wss://advanced-trade-ws.coinbase.com"
COINBASE_WS_PRODUCT: Final[str] = "BTC-USD"
COINBASE_WS_CHANNEL: Final[str] = "market_trades"

#: Polymarket RTDS channel that re-emits Chainlink BTC oracle prices.
RTDS_CRYPTO_PRICES_CHANNEL: Final[str] = "crypto_prices"

# ============================================================================
# Slot timing
# ============================================================================
# Polymarket BTC up/down 5-minute slot boundaries are deterministic on UTC.
# unix_ts % 300 == 0 is the boundary. The slot N runs [boundary, boundary+300).

#: Slot length in seconds.
SLOT_DURATION_S: Final[int] = 300

#: Slot length in milliseconds.
SLOT_DURATION_MS: Final[int] = SLOT_DURATION_S * 1000

#: Slug template for the BTC up/down 5-minute market.
#: Slot end timestamp (the resolution time, in seconds since epoch) is appended.
BTC_UPDOWN_5M_SLUG_TEMPLATE: Final[str] = "btc-updown-5m-{slot_end_ts}"

# ============================================================================
# Sanity self-check
# ============================================================================


def assert_constants_sane() -> None:
    """Light invariant check; called at engine startup."""
    assert SLOT_DURATION_S == 300
    assert MIN_ORDER_SHARES >= 1
    assert BATCH_ORDER_LIMIT >= 1
    assert FEE_QUANTUM > 0
    assert PUSD_DECIMALS == 6
    assert CTF_EXCHANGE_V2_ADDRESS.startswith("0x")
    assert NEG_RISK_CTF_EXCHANGE_V2_ADDRESS.startswith("0x")
    assert EIP712_EXCHANGE_DOMAIN_VERSION == "2"
