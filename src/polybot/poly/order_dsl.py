"""Order DSL — strategy-facing types over the SDK's lower-level shapes.

`OrderRequest` is what strategy code constructs. The wrapper translates it
into the SDK's `OrderArgsV2` + `PartialCreateOrderOptions` for submission.
Decimals are used for prices and sizes throughout to avoid float drift in
fee math (per §6.3.5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Subset of SDK OrderType — the four we will use."""

    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


class OrderStatus(str, Enum):
    PENDING = "PENDING"           # local: built but not yet posted
    LIVE = "LIVE"                 # acknowledged by exchange
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass(slots=True)
class OrderRequest:
    """What the strategy hands to PolyClient.place_order(). Engine-side abstraction."""

    token_id: str
    side: Side
    price: Decimal
    shares: int
    order_type: OrderType = OrderType.GTC
    post_only: bool = True
    expire_at_ms: int | None = None       # engine-side ms; SDK takes seconds
    client_tag: str = ""                  # for our records — never sent to exchange
    on_filled: Callable[[int], None] | None = None
    on_expired: Callable[[], None] | None = None
    on_failed: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        if self.shares < 1:
            raise ValueError(f"shares must be >= 1; got {self.shares}")
        if not (Decimal("0") < self.price < Decimal("1")):
            raise ValueError(f"price must be in (0, 1) for binary markets; got {self.price}")
        if self.expire_at_ms is not None and self.expire_at_ms <= 0:
            raise ValueError("expire_at_ms must be a positive ms timestamp")
        if self.order_type is OrderType.GTD and self.expire_at_ms is None:
            raise ValueError("GTD order requires expire_at_ms")


@dataclass(slots=True, frozen=True)
class PlacedOrder:
    """Returned from PolyClient.place_order() once the SDK accepts it."""

    order_id: str
    token_id: str
    side: Side
    price: Decimal
    shares: int
    posted_at_ms: int
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class BookLevel:
    price: Decimal
    size: Decimal


@dataclass(slots=True, frozen=True)
class OrderBookSnapshot:
    """One-shot REST/WS snapshot of an order book."""

    token_id: str
    bids: tuple[BookLevel, ...]      # sorted descending by price
    asks: tuple[BookLevel, ...]      # sorted ascending by price
    timestamp_ms: int

    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None

    def mid(self) -> Decimal | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb.price + ba.price) / Decimal(2)

    def spread_bps(self) -> Decimal | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        m = (bb.price + ba.price) / Decimal(2)
        if m <= 0:
            return None
        return ((ba.price - bb.price) / m) * Decimal(10_000)


@dataclass(slots=True, frozen=True)
class FeeDetails:
    """Per-market fee schedule, returned by getClobMarketInfo."""

    fee_rate: Decimal           # e.g. 0.25 (current crypto schedule)
    exponent: int               # e.g. 2 (current) or 1 (post-Mar-30)
    maker_rebate_fraction: Decimal


@dataclass(slots=True, frozen=True)
class ClobMarketInfo:
    condition_id: str
    tick_size: Decimal
    min_order_size: int
    fee_details: FeeDetails
    neg_risk: bool
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class Balances:
    pusd: Decimal                              # collateral balance (in pUSD whole units)
    conditional: dict[str, Decimal]            # token_id -> share balance


@dataclass(slots=True, frozen=True)
class MarketHandle:
    """Result of slug -> condition_id resolution."""

    slug: str
    condition_id: str
    token_ids: tuple[str, str]                 # (UP, DOWN)
    slot_start_ms: int
    slot_end_ms: int
    tick_size: Decimal
    min_order_size: int
    neg_risk: bool


@dataclass(slots=True, frozen=True)
class OpenOrder:
    """Subset of fields from SDK get_open_orders()."""

    order_id: str
    token_id: str
    side: Side
    price: Decimal
    shares_remaining: int
    status: Literal["LIVE", "PARTIAL"]
    raw: dict[str, object] = field(default_factory=dict)
