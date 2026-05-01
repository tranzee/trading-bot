# Phase 1 — SDK deviations from MASTER_BLUEPRINT.md §6.3.1

Source of truth probe: `py-clob-client-v2==1.0.0` installed and inspected
2026-04-26. The §6.3.1 spec was written against an idealized API; the real
SDK shape is mostly aligned but not identical. Per the operator instruction
"defer to the real SDK shape and update the blueprint," this file records
the concrete deviations and our wrapper's response.

## Method-by-method

| §6.3.1 spec | Real SDK | Wrapper response |
|-|-|-|
| `place_order(req: OrderRequest, post_only: bool = True) -> PlacedOrder` | `ClobClient.create_and_post_order(order_args: OrderArgsV2, options: PartialCreateOrderOptions = None, order_type: OrderType = "GTC", post_only: bool = False, defer_exec: bool = False)` | `PolyClient.place_order(req: OrderRequest, post_only=True)` builds `OrderArgsV2` + `PartialCreateOrderOptions` from our internal `OrderRequest` and calls `create_and_post_order` with `post_only=True` by default. |
| `cancel_order(order_id: str) -> bool` | `ClobClient.cancel_order(payload: OrderPayload)` where `OrderPayload(orderID=...)` | Wrapper accepts a string and constructs `OrderPayload`. |
| `cancel_all(token_ids: list[str]) -> int` | `ClobClient.cancel_all()` — no args; cancels ALL of the authenticated user's open orders. To cancel only a subset use `cancel_orders(payloads)` or `cancel_market_orders(...)`. | `PolyClient.cancel_all_for_tokens(token_ids)` uses `cancel_market_orders(condition_id, asset_id)` per token to limit blast radius. `PolyClient.cancel_all_global()` exposes the nuclear option for shutdown. |
| `get_book(token_id: str) -> OrderBook` | `ClobClient.get_order_book(token_id: str) -> dict` | Method renamed to `get_order_book`; we parse the dict into our typed `OrderBookSnapshot`. |
| `get_clob_market_info(condition_id: str) -> ClobMarketInfo` | `ClobClient.get_clob_market_info(condition_id: str) -> dict` | Matches name; we type the dict into `ClobMarketInfo`. |
| `get_balance() -> Balances` | `ClobClient.get_balance_allowance(params: BalanceAllowanceParams)` where `BalanceAllowanceParams(asset_type=AssetType.COLLATERAL | CONDITIONAL, token_id=..., signature_type=...)` | `PolyClient.get_balances(token_ids: list[str])` makes one call for collateral + one per token, returns `Balances{ pusd: Decimal, conditional: dict[str, Decimal] }`. |
| `merge_positions(token_ids, shares) -> TxHash` | **Not in SDK.** This is a direct `ConditionalTokens.mergePositions(...)` call on Polygon. | Deferred to a future `poly/conditional_tokens.py` module (web3 contract calls). Not needed for Phase 1 acceptance. Logged as TODO. |
| `redeem(token_id) -> TxHash` | **Not in SDK.** Direct `ConditionalTokens.redeemPositions(...)` call. | Same — deferred to `poly/conditional_tokens.py`. |
| `setup_heartbeat(cancel_after_ms: int) -> None` | `ClobClient.post_heartbeat(heartbeat_id: str = "") -> dict`. The dead-man cancel timing is server-side; the client's responsibility is to **call this on a regular cadence**, and the server cancels if it doesn't hear from us within the configured window. | `PolyClient.start_heartbeat(period_s: float = 1.0)` spawns an async task that calls `post_heartbeat` every `period_s`. The server-side cancel window is a separate Polymarket configuration. |
| `OrderRequest.side: Literal['BUY', 'SELL']` | `Side` is an `IntEnum`: `BUY = 0`, `SELL = 1`. The SDK's `OrderArgsV2.side: str` accepts the strings `"BUY"`/`"SELL"` (it's the enum's name). | Wrapper uses string-typed `Side` in our DSL and passes through. |
| `OrderRequest.expire_at_ms: Optional[int]` | `OrderArgsV2.expiration: int = 0` (zero means GTC, otherwise unix-seconds expiry). Note the **seconds** not ms. | Wrapper converts our ms-precision field to seconds before SDK call. |
| §2.1 order struct field `metadata: bytes32` | `OrderArgsV2.metadata: str` defaulting to `0x000…` (32-byte hex string) | Match. We may attach a deterministic per-slot tag in later phases. |
| `builderCode` per-order | Two attachment points: `OrderArgsV2.builder_code` and `BuilderConfig` passed to the `ClobClient` constructor. The `builder_code` on the order is what the protocol attributes; `BuilderConfig` is the builder's own auth context for retrieving builder analytics. | Per-order `builder_code` from `POLYMARKET_BUILDER_CODE` env var. `BuilderConfig` is unused unless the operator opts in. |

## What the SDK does NOT provide (we build directly)

1. **WebSocket order book / trade subscriptions.** The SDK is REST-only.
   Phase 1's `poly/orderbook.py` connects directly to Polymarket's WS host
   for the `market` (book) channel.
2. **WebSocket user channel.** Real-time order/fill events come via WS; the
   SDK's `get_open_orders`/`get_order` are REST and lag. Phase 1 includes
   a WS user-channel subscriber alongside the REST polling fallback.
3. **pUSD wrap/unwrap.** Direct `web3` calls to the Collateral Onramp
   contract — built in `poly/pusd.py`.
4. **`merge_positions` / `redeem`.** Direct `ConditionalTokens` contract
   calls — deferred to a future `poly/conditional_tokens.py`.
5. **Slug -> condition_id resolution.** Polymarket's Gamma API
   (`https://gamma-api.polymarket.com/events?slug=...`) — a separate
   HTTP service that the SDK does not wrap. Built in
   `poly/market_discovery.py` using `httpx`.

## Implications for §6.3.1

Adopting the SDK shape verbatim means our wrapper public surface looks like:

```python
class PolyClient:
    async def place_order(self, req: OrderRequest, *, post_only: bool = True) -> PlacedOrder: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def cancel_all_for_tokens(self, token_ids: list[str]) -> int: ...
    async def cancel_all_global(self) -> int: ...
    async def get_order_book(self, token_id: str) -> OrderBookSnapshot: ...
    async def get_clob_market_info(self, condition_id: str) -> ClobMarketInfo: ...
    async def get_balances(self, token_ids: list[str] | None = None) -> Balances: ...
    async def get_open_orders(self, market: str | None = None) -> list[OpenOrder]: ...
    async def get_tick_size(self, token_id: str) -> Decimal: ...
    async def get_neg_risk(self, token_id: str) -> bool: ...
    async def post_heartbeat(self, heartbeat_id: str = "") -> None: ...
    async def start_heartbeat(self, period_s: float = 1.0) -> asyncio.Task: ...
```

`merge_positions` and `redeem` move to a separate `ConditionalTokens` helper.

## Behavioral notes

- The SDK is **synchronous**. Our wrapper runs SDK calls inside
  `asyncio.to_thread()` so the bot's event loop is not blocked. This is
  documented per-method.
- The SDK's `retry_on_error: bool = False` constructor flag enables a
  built-in single retry. We leave it `False` and apply our own
  retry-with-backoff + circuit-breaker decorator (`obs/retry.py`,
  added in Phase 1).
- The SDK's `use_server_time: bool = False` flag enables clock-sync to the
  server's `/time` endpoint for signing. We enable this in production
  (`use_server_time=True`) since clock drift > 1s breaks signed orders.

## Status

This deviation log is the source of truth for §6.3.1 going forward. The
blueprint will be patched in a later session. Until then, this file
governs.
