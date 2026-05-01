"""Polymarket CLOB V2 client wrapper.

Wraps `py-clob-client-v2==1.0.0` directly. No vendor-portability seam.
The real SDK is synchronous; we run calls on a worker thread via
`asyncio.to_thread()`. Every public method has timeout, retry-with-backoff,
and circuit-breaker integration (see `polybot.obs.retry`).

Surface differs from MASTER_BLUEPRINT.md §6.3.1 — see
`docs/PHASE1_SDK_DEVIATIONS.md` for the full deviation log.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from py_clob_client_v2 import ClobClient
from py_clob_client_v2.clob_types import (
    AssetType,
    BalanceAllowanceParams,
    BuilderConfig,
    OpenOrderParams,
    OrderArgsV2,
    OrderPayload,
    OrderType as SdkOrderType,
    PartialCreateOrderOptions,
)
from py_clob_client_v2.exceptions import PolyApiException

from config import constants as K
from config.settings import Settings
from polybot.obs.logger import log
from polybot.obs.retry import CircuitBreaker, retry, with_timeout
from polybot.poly.order_dsl import (
    Balances,
    BookLevel,
    ClobMarketInfo,
    FeeDetails,
    OpenOrder,
    OrderBookSnapshot,
    OrderRequest,
    OrderType,
    PlacedOrder,
    Side,
)

if TYPE_CHECKING:
    from py_clob_client_v2.clob_types import ApiCreds


# Single circuit breaker per client instance. Reused by every wrapped call so
# that a sustained Polymarket outage trips ALL methods, not just the one in use.
_DEFAULT_CIRCUIT = CircuitBreaker(name="poly", failure_threshold=5, cooldown_s=30.0)


_RETRYABLE = (PolyApiException, ConnectionError, TimeoutError, asyncio.TimeoutError)


def _ms_to_unix_seconds(ms: int) -> int:
    """Convert engine-side millisecond timestamps to SDK's expected unix-seconds."""
    return ms // 1000


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


class PolyClient:
    """Async wrapper around `py-clob-client-v2.ClobClient`.

    Construction reads the loaded `Settings` and instantiates the SDK with
    builder code (if present) and `use_server_time=True` to defend against
    local clock drift. API credentials are derived once on first use.
    """

    def __init__(self, settings: Settings, *, circuit: CircuitBreaker | None = None) -> None:
        self._settings = settings
        self._circuit = circuit or _DEFAULT_CIRCUIT
        self._creds: ApiCreds | None = None
        self._client: ClobClient | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    # ----- Construction -------------------------------------------------------

    def _builder_config(self) -> BuilderConfig | None:
        bc = self._settings.POLYMARKET_BUILDER_CODE
        if not bc:
            return None
        # Builder code may be a 32-byte hex string (0x...) or a builder addr.
        return BuilderConfig(builder_address="", builder_code=bc)

    def _build_unauthenticated(self) -> ClobClient:
        return ClobClient(
            host=self._settings.POLYMARKET_HOST,
            chain_id=K.POLYGON_CHAIN_ID,
            key=self._settings.POLYMARKET_PRIVATE_KEY.get_secret_value(),
            funder=self._settings.POLYMARKET_FUNDER_ADDRESS,
            builder_config=self._builder_config(),
            use_server_time=True,
            retry_on_error=False,  # we apply our own retry
        )

    async def setup_creds(self) -> None:
        """Idempotent: derive (or create) L2 API credentials and attach them."""
        if self._client is not None and self._creds is not None:
            return

        def _build_and_derive() -> tuple[ClobClient, ApiCreds]:
            client = self._build_unauthenticated()
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            return client, creds

        client, creds = await asyncio.to_thread(_build_and_derive)
        self._client = client
        self._creds = creds
        log.info(
            "poly.client: API creds ready (api_key={}, host={})",
            creds.api_key[:8] + "...",
            self._settings.POLYMARKET_HOST,
        )

    def _require_client(self) -> ClobClient:
        if self._client is None:
            raise RuntimeError("PolyClient.setup_creds() must be awaited before use")
        return self._client

    # ----- Order placement ----------------------------------------------------

    @retry(
        attempts=4,
        base_delay_s=0.2,
        max_delay_s=4.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.place_order",
    )
    async def place_order(
        self,
        req: OrderRequest,
        *,
        post_only: bool = True,
        timeout_s: float = 5.0,
    ) -> PlacedOrder:
        """Build and post an order.

        post_only defaults to True (maker-first; never crosses the spread). Set
        False only for explicit emergency taker exits or extreme-tail entries.
        """
        client = self._require_client()
        sdk_args = OrderArgsV2(
            token_id=req.token_id,
            price=float(req.price),
            size=float(req.shares),
            side=req.side.value,  # SDK accepts "BUY" / "SELL"
            expiration=(
                _ms_to_unix_seconds(req.expire_at_ms) if req.expire_at_ms else 0
            ),
            builder_code=self._settings.POLYMARKET_BUILDER_CODE
            or "0x" + "0" * 64,
        )
        sdk_options = PartialCreateOrderOptions()  # tick_size/neg_risk auto-fetched
        sdk_type = SdkOrderType(req.order_type.value)

        def _post() -> dict[str, Any]:
            return cast(
                dict[str, Any],
                client.create_and_post_order(
                    order_args=sdk_args,
                    options=sdk_options,
                    order_type=sdk_type,
                    post_only=post_only,
                ),
            )

        raw = await with_timeout(asyncio.to_thread(_post), timeout_s, label="place_order")
        order_id = str(raw.get("orderID") or raw.get("order_id") or raw.get("id") or "")
        if not order_id:
            raise PolyApiException(  # type: ignore[call-arg]
                f"place_order returned no order id; raw={raw!r}"
            )
        return PlacedOrder(
            order_id=order_id,
            token_id=req.token_id,
            side=req.side,
            price=req.price,
            shares=req.shares,
            posted_at_ms=_now_ms(),
            raw=raw,
        )

    @retry(
        attempts=3,
        base_delay_s=0.1,
        max_delay_s=1.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.cancel_order",
    )
    async def cancel_order(self, order_id: str, *, timeout_s: float = 3.0) -> bool:
        client = self._require_client()
        payload = OrderPayload(orderID=order_id)
        result = await with_timeout(
            asyncio.to_thread(client.cancel_order, payload),
            timeout_s,
            label="cancel_order",
        )
        log.info("poly.cancel_order: id={} ok={}", order_id, bool(result))
        return bool(result)

    @retry(
        attempts=3,
        base_delay_s=0.1,
        max_delay_s=1.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.cancel_all_global",
    )
    async def cancel_all_global(self, *, timeout_s: float = 5.0) -> int:
        """Cancel ALL open orders for the authenticated user (nuclear option)."""
        client = self._require_client()
        result = await with_timeout(
            asyncio.to_thread(client.cancel_all),
            timeout_s,
            label="cancel_all",
        )
        # SDK returns a list of canceled order IDs (or a dict); normalize to count
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict):
            canceled = result.get("canceled") or result.get("cancelled") or []
            return len(canceled) if isinstance(canceled, list) else 0
        return 0

    # ----- Reads --------------------------------------------------------------

    @retry(
        attempts=4,
        base_delay_s=0.1,
        max_delay_s=2.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.get_order_book",
    )
    async def get_order_book(self, token_id: str, *, timeout_s: float = 3.0) -> OrderBookSnapshot:
        client = self._require_client()
        raw = await with_timeout(
            asyncio.to_thread(client.get_order_book, token_id),
            timeout_s,
            label="get_order_book",
        )
        return _parse_order_book(token_id, raw)

    @retry(
        attempts=4,
        base_delay_s=0.1,
        max_delay_s=2.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.get_clob_market_info",
    )
    async def get_clob_market_info(
        self, condition_id: str, *, timeout_s: float = 3.0
    ) -> ClobMarketInfo:
        client = self._require_client()
        raw = await with_timeout(
            asyncio.to_thread(client.get_clob_market_info, condition_id),
            timeout_s,
            label="get_clob_market_info",
        )
        return _parse_market_info(condition_id, raw)

    @retry(
        attempts=4,
        base_delay_s=0.1,
        max_delay_s=2.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.get_tick_size",
    )
    async def get_tick_size(self, token_id: str, *, timeout_s: float = 3.0) -> Decimal:
        client = self._require_client()
        raw = await with_timeout(
            asyncio.to_thread(client.get_tick_size, token_id),
            timeout_s,
            label="get_tick_size",
        )
        return Decimal(str(raw))

    @retry(
        attempts=4,
        base_delay_s=0.1,
        max_delay_s=2.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.get_neg_risk",
    )
    async def get_neg_risk(self, token_id: str, *, timeout_s: float = 3.0) -> bool:
        client = self._require_client()
        return cast(
            bool,
            await with_timeout(
                asyncio.to_thread(client.get_neg_risk, token_id),
                timeout_s,
                label="get_neg_risk",
            ),
        )

    @retry(
        attempts=3,
        base_delay_s=0.2,
        max_delay_s=2.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.get_balances",
    )
    async def get_balances(
        self,
        token_ids: list[str] | None = None,
        *,
        timeout_s: float = 3.0,
    ) -> Balances:
        client = self._require_client()

        def _fetch() -> Balances:
            collateral = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            pusd_balance = _decimal_from_balance(collateral)
            cond: dict[str, Decimal] = {}
            for tid in token_ids or []:
                row = client.get_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tid)
                )
                cond[tid] = _decimal_from_balance(row)
            return Balances(pusd=pusd_balance, conditional=cond)

        return await with_timeout(asyncio.to_thread(_fetch), timeout_s, label="get_balances")

    @retry(
        attempts=3,
        base_delay_s=0.2,
        max_delay_s=2.0,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.get_open_orders",
    )
    async def get_open_orders(
        self,
        market: str | None = None,
        *,
        timeout_s: float = 5.0,
    ) -> list[OpenOrder]:
        client = self._require_client()
        params = OpenOrderParams(market=market) if market else OpenOrderParams()
        raw = await with_timeout(
            asyncio.to_thread(client.get_open_orders, params),
            timeout_s,
            label="get_open_orders",
        )
        return [_parse_open_order(o) for o in (raw or [])]

    # ----- Heartbeat (dead-man cancel) ----------------------------------------

    @retry(
        attempts=2,
        base_delay_s=0.1,
        max_delay_s=0.5,
        retry_on=_RETRYABLE,
        circuit=_DEFAULT_CIRCUIT,
        label="poly.post_heartbeat",
    )
    async def post_heartbeat(self, heartbeat_id: str = "", *, timeout_s: float = 2.0) -> None:
        client = self._require_client()
        await with_timeout(
            asyncio.to_thread(client.post_heartbeat, heartbeat_id),
            timeout_s,
            label="post_heartbeat",
        )

    async def start_heartbeat(self, period_s: float = 1.0, heartbeat_id: str = "") -> asyncio.Task[None]:
        """Spawn a background task that pings the heartbeat endpoint at `period_s` cadence.

        The server-side cancel-after window is configured separately on
        Polymarket; this client's responsibility is to keep calling.
        """
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return self._heartbeat_task

        async def _loop() -> None:
            try:
                while True:
                    try:
                        await self.post_heartbeat(heartbeat_id)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("heartbeat: post failed: {}", exc)
                    await asyncio.sleep(period_s)
            except asyncio.CancelledError:
                log.info("heartbeat: task cancelled")
                raise

        self._heartbeat_task = asyncio.create_task(_loop(), name="poly-heartbeat")
        return self._heartbeat_task

    async def stop_heartbeat(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._heartbeat_task = None


# ============================================================================
# Parsers (raw SDK dict -> typed DSL)
# ============================================================================


def _decimal_from_balance(row: Any) -> Decimal:
    """SDK BalanceAllowance return is a dict-or-object with 'balance' field."""
    if isinstance(row, dict):
        b = row.get("balance") or row.get("Balance") or "0"
    else:
        b = getattr(row, "balance", "0")
    s = str(b)
    # Strip wei -> whole units. Polymarket exposes balances as 6-decimal (USDC) wei strings.
    if s.isdigit() and len(s) > 6:
        return Decimal(s) / (Decimal(10) ** K.PUSD_DECIMALS)
    try:
        return Decimal(s)
    except Exception:
        return Decimal(0)


def _parse_order_book(token_id: str, raw: Any) -> OrderBookSnapshot:
    if isinstance(raw, dict):
        bids_raw = raw.get("bids") or []
        asks_raw = raw.get("asks") or []
        ts = int(raw.get("timestamp") or raw.get("ts") or _now_ms())
    else:
        bids_raw = getattr(raw, "bids", []) or []
        asks_raw = getattr(raw, "asks", []) or []
        ts = int(getattr(raw, "timestamp", _now_ms()))

    def _level(item: Any) -> BookLevel:
        if isinstance(item, dict):
            p = item.get("price") or item.get("p")
            s = item.get("size") or item.get("s") or item.get("amount")
        else:
            p = getattr(item, "price", None) or getattr(item, "p", None)
            s = getattr(item, "size", None) or getattr(item, "s", None)
        return BookLevel(price=Decimal(str(p)), size=Decimal(str(s)))

    bids = tuple(sorted((_level(b) for b in bids_raw), key=lambda lvl: lvl.price, reverse=True))
    asks = tuple(sorted((_level(a) for a in asks_raw), key=lambda lvl: lvl.price))
    return OrderBookSnapshot(token_id=token_id, bids=bids, asks=asks, timestamp_ms=ts)


def _parse_market_info(condition_id: str, raw: Any) -> ClobMarketInfo:
    d = raw if isinstance(raw, dict) else {}

    fee_rate = Decimal(str(d.get("fee_rate", K.FEE_RATE_CRYPTO_CURRENT)))
    exponent = int(d.get("fee_rate_exponent", d.get("exponent", K.FEE_EXPONENT_CRYPTO_CURRENT)))
    rebate = Decimal(str(d.get("maker_rebate_fraction", K.MAKER_REBATE_FRACTION)))
    tick_size = Decimal(str(d.get("mts", d.get("tick_size", K.DEFAULT_TICK_SIZE))))
    min_size = int(d.get("min_order_size", K.MIN_ORDER_SHARES))
    neg_risk = bool(d.get("neg_risk", False))

    return ClobMarketInfo(
        condition_id=condition_id,
        tick_size=tick_size,
        min_order_size=min_size,
        fee_details=FeeDetails(
            fee_rate=fee_rate,
            exponent=exponent,
            maker_rebate_fraction=rebate,
        ),
        neg_risk=neg_risk,
        raw=d,
    )


def _parse_open_order(raw: Any) -> OpenOrder:
    d = raw if isinstance(raw, dict) else {}
    side_str = str(d.get("side", "BUY")).upper()
    side = Side.BUY if side_str == "BUY" else Side.SELL
    return OpenOrder(
        order_id=str(d.get("id") or d.get("orderID") or ""),
        token_id=str(d.get("asset_id") or d.get("token_id") or ""),
        side=side,
        price=Decimal(str(d.get("price", "0"))),
        shares_remaining=int(float(d.get("size_matched", 0))) if d.get("status") == "PARTIAL"
        else int(float(d.get("size_remaining", d.get("size", 0)))),
        status=cast(Any, d.get("status", "LIVE")),
        raw=d,
    )


__all__ = [
    "OrderType",
    "PolyClient",
]
