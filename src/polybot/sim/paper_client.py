"""Phase 7 — Paper (simulated) CLOB client.

Drop-in replacement for `poly/client.py` in simulation mode. All order
calls are virtual: no network I/O, fills are simulated by `fill_model.py`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

from polybot.obs.logger import log
from polybot.poly.order_dsl import OrderRequest


@dataclass
class PaperOrderResult:
    order_id: str
    status: str = "LIVE"


@dataclass
class PaperOrder:
    order_id: str
    token_id: str
    price: Decimal
    shares: Decimal
    status: str = "LIVE"
    filled_shares: Decimal = Decimal("0")


class PaperClient:
    """Simulates a CLOB client without any real API calls.

    State is in-memory only; reset between simulation rounds.
    """

    def __init__(self) -> None:
        self._orders: dict[str, PaperOrder] = {}
        self._order_seq = 0
        self._heartbeats = 0

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(self, req: OrderRequest, **_kw) -> PaperOrderResult:
        self._order_seq += 1
        oid = f"paper_{self._order_seq:06d}"
        self._orders[oid] = PaperOrder(
            order_id=oid,
            token_id=req.token_id,
            price=req.price,
            shares=req.shares,
        )
        log.debug("paper_client: placed order {}", oid)
        return PaperOrderResult(order_id=oid)

    async def cancel_order(self, order_id: str, **_kw) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "CANCELLED"
            log.debug("paper_client: cancelled order {}", order_id)
            return True
        return False

    async def get_open_orders(self, *_a, **_kw) -> list[PaperOrder]:
        return [o for o in self._orders.values() if o.status == "LIVE"]

    async def get_order_book(self, token_id: str, **_kw):
        from polybot.poly.orderbook import OrderBookSnapshot
        return OrderBookSnapshot(token_id=token_id, bids=(), asks=(), timestamp_ms=int(time.time() * 1000))

    async def post_heartbeat(self, *_a, **_kw) -> None:
        self._heartbeats += 1

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------

    def simulate_fill(self, order_id: str) -> bool:
        """Mark an order as filled. Returns True if order existed and was LIVE."""
        o = self._orders.get(order_id)
        if o and o.status == "LIVE":
            o.status = "MATCHED"
            o.filled_shares = o.shares
            return True
        return False

    def reset(self) -> None:
        self._orders.clear()
        self._order_seq = 0
