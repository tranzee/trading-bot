"""Phase 4 — Price-action maker strategy.

Translates a PA-engine Signal into a post-only maker OrderRequest:
    - DOWN signal → buy DOWN token at maker_price_down (default 0.49)
    - UP signal   → buy UP token at maker_price_up (default 0.51)

Maker prices are set slightly below the fair-value coin-flip to maximise
fill probability while staying in the maker rebate zone.
"""

from __future__ import annotations

from decimal import Decimal

from polybot.engine.strategy_context import StrategyBase
from polybot.poly.order_dsl import MarketHandle, OrderRequest, Side
from polybot.signal.models import Signal, SignalDirection
from polybot.truth.models import Candle


class PriceActionMakerStrategy(StrategyBase):
    """Flagship strategy: price-action signal → post-only maker order."""

    def __init__(
        self,
        *,
        maker_price_down: Decimal = Decimal("0.49"),
        maker_price_up: Decimal = Decimal("0.51"),
    ) -> None:
        self._price_down = maker_price_down
        self._price_up = maker_price_up

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

        is_down = signal.direction is SignalDirection.DOWN
        entry_price = self._price_down if is_down else self._price_up
        token_id = market.token_ids[1] if is_down else market.token_ids[0]

        shares = (size_usd / entry_price).quantize(Decimal("0.01"))
        if shares <= 0 or shares < market.min_order_size:
            return None

        return OrderRequest(
            token_id=token_id,
            side=Side.BUY,
            price=entry_price,
            shares=shares,
            post_only=True,
        )
