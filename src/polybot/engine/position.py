"""Per-slot position state (Phase 4+).

One SlotPosition is created when a signal fires and we decide to enter.
It tracks the order lifecycle from placement through settlement.
Paper positions never touch the CLOB; live positions have a real order_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class PositionStatus(str, Enum):
    PENDING  = "pending"    # order placed, not yet confirmed filled
    FILLED   = "filled"     # fill confirmed (paper: immediate; live: WS/REST)
    CANCELLED = "cancelled" # order cancelled before fill (timeout or manual)
    SETTLED  = "settled"    # slot resolved; outcome recorded


@dataclass
class SlotPosition:
    """Tracks one slot's position from entry to settlement."""

    direction: str           # "UP" or "DOWN"
    token_id: str
    shares: Decimal
    entry_price: Decimal     # price at which we bought (maker price or paper mid)
    slot_end_ms: int         # unix-ms when the slot settles
    paper: bool = True
    order_id: str | None = None
    status: PositionStatus = PositionStatus.PENDING
    fill_price: Decimal | None = None
    settled_win: bool | None = None   # True=WIN, False=LOSE, None=unknown

    # Derived P&L fields (populated on settlement)
    gross_pnl_usd: Decimal = Decimal("0")
    net_pnl_usd: Decimal = Decimal("0")

    def record_fill(self, fill_price: Decimal) -> None:
        self.fill_price = fill_price
        self.status = PositionStatus.FILLED

    def record_settlement(self, *, won: bool, maker_rebate_usd: Decimal = Decimal("0")) -> None:
        """Compute P&L from settlement outcome.

        For a DOWN position:
          - Entry: buy DOWN at entry_price (e.g. 0.49)
          - Win: DOWN resolves 1.00 → payout = shares × (1.00 - entry_price)
          - Lose: DOWN resolves 0.00 → loss = shares × entry_price
        """
        self.settled_win = won
        self.status = PositionStatus.SETTLED
        cost = self.shares * self.entry_price
        if won:
            self.gross_pnl_usd = self.shares * (Decimal("1") - self.entry_price)
        else:
            self.gross_pnl_usd = -(cost)
        self.net_pnl_usd = self.gross_pnl_usd + maker_rebate_usd

    def cancel(self) -> None:
        self.status = PositionStatus.CANCELLED
