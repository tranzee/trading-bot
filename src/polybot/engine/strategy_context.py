"""Phase 4 — Strategy abstraction.

Each strategy implements `decide()` to translate a PA-engine Signal into
an OrderRequest (or None to skip the slot). The SlotLoop owns the outer
event loop; the strategy owns the trade decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from polybot.poly.order_dsl import MarketHandle, OrderRequest
from polybot.signal.models import Signal
from polybot.truth.models import Candle


class StrategyBase(ABC):
    """Abstract base for all strategy implementations."""

    @abstractmethod
    def decide(
        self,
        signal: Signal,
        market: MarketHandle,
        candle: Candle,
        *,
        size_usd: Decimal,
    ) -> OrderRequest | None:
        """Return an OrderRequest to place, or None to pass on this signal.

        Args:
            signal:   PA-engine signal (direction, confidence, etc.)
            market:   Resolved MarketHandle for the current slot.
            candle:   The candle whose close fired the signal.
            size_usd: Kelly-sized position size in USD (from PositionSizer).
        """
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__
