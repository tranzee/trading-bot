"""Phase 4 — Simulation strategy (always-buy-at-0.49 dummy).

Used to validate the engine plumbing end-to-end without requiring a
real PA signal. On every signal, buys the DOWN token at 0.49.
"""

from __future__ import annotations

from decimal import Decimal

from polybot.engine.strategy_context import StrategyBase
from polybot.poly.order_dsl import MarketHandle, OrderRequest, Side
from polybot.signal.models import Signal
from polybot.truth.models import Candle

_FIXED_PRICE = Decimal("0.49")


class SimulationStrategy(StrategyBase):
    """Always buys the DOWN token at 0.49, regardless of signal direction.

    Only purpose: validate order placement / fill / settlement plumbing.
    Never use in live mode.
    """

    def decide(
        self,
        signal: Signal,
        market: MarketHandle,
        candle: Candle,
        *,
        size_usd: Decimal,
    ) -> OrderRequest | None:
        if len(market.token_ids) < 2:
            return None
        shares = (size_usd / _FIXED_PRICE).quantize(Decimal("0.01"))
        if shares <= 0 or shares < market.min_order_size:
            return None
        return OrderRequest(
            token_id=market.token_ids[1],  # DOWN token
            side=Side.BUY,
            price=_FIXED_PRICE,
            shares=shares,
            post_only=True,
        )
