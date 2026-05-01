"""Phase 7 — Realistic fill model for paper simulation.

Determines whether a maker order gets filled given current book state.
Simple model: fill if our price >= best_ask (aggressive enough to get hit).
For paper mode the order is always filled immediately at maker price.
"""

from __future__ import annotations

from decimal import Decimal

from polybot.poly.orderbook import OrderBookSnapshot


def should_fill(price: Decimal, book: OrderBookSnapshot | None) -> bool:
    """Return True if a maker BUY at `price` would fill against `book`.

    Conservatively assumes fill when no book data is available (fail-open
    in paper mode so simulation always produces trades to test plumbing).
    """
    if book is None or not book.asks:
        return True  # no book data → optimistic fill for paper
    best_ask = min(lvl.price for lvl in book.asks)
    return price >= best_ask
