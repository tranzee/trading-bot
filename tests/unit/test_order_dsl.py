"""Order DSL invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polybot.poly.order_dsl import (
    BookLevel,
    OrderBookSnapshot,
    OrderRequest,
    OrderType,
    Side,
)


def test_order_request_rejects_zero_shares() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            token_id="t", side=Side.BUY, price=Decimal("0.5"), shares=0,
        )


def test_order_request_rejects_price_outside_unit_interval() -> None:
    for bad in ("0", "1", "1.01", "-0.1"):
        with pytest.raises(ValueError):
            OrderRequest(
                token_id="t", side=Side.BUY, price=Decimal(bad), shares=10,
            )


def test_gtd_requires_expiry() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            token_id="t", side=Side.BUY, price=Decimal("0.5"), shares=10,
            order_type=OrderType.GTD,
        )


def test_book_snapshot_best_levels_and_spread() -> None:
    snap = OrderBookSnapshot(
        token_id="t",
        bids=(BookLevel(Decimal("0.49"), Decimal("100")), BookLevel(Decimal("0.48"), Decimal("50"))),
        asks=(BookLevel(Decimal("0.51"), Decimal("100")), BookLevel(Decimal("0.52"), Decimal("75"))),
        timestamp_ms=1000,
    )
    assert snap.best_bid() is not None and snap.best_bid().price == Decimal("0.49")
    assert snap.best_ask() is not None and snap.best_ask().price == Decimal("0.51")
    assert snap.mid() == Decimal("0.50")
    bps = snap.spread_bps()
    assert bps is not None
    assert bps == Decimal("400")  # 2 cents on 0.50 mid = 400 bps


def test_book_snapshot_handles_empty_side() -> None:
    snap = OrderBookSnapshot(token_id="t", bids=(), asks=(), timestamp_ms=0)
    assert snap.best_bid() is None
    assert snap.best_ask() is None
    assert snap.mid() is None
    assert snap.spread_bps() is None
