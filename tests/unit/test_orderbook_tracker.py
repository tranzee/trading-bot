"""OrderBookTracker delta/snapshot semantics — offline only (no WS)."""

from __future__ import annotations

import json
from decimal import Decimal

from polybot.poly.orderbook import BookUpdate, OrderBookTracker, _BookState


def test_apply_snapshot_then_delta() -> None:
    state = _BookState()
    state.apply_snapshot(
        {
            "bids": [{"price": "0.49", "size": "100"}, {"price": "0.48", "size": "50"}],
            "asks": [{"price": "0.51", "size": "100"}, {"price": "0.52", "size": "75"}],
        }
    )
    snap = state.snapshot("t")
    assert snap.best_bid().price == Decimal("0.49")
    assert snap.best_ask().price == Decimal("0.51")

    # delta: best ask filled, new best ask appears
    state.apply_delta({"changes": [{"side": "SELL", "price": "0.51", "size": "0"}]})
    snap = state.snapshot("t")
    assert snap.best_ask().price == Decimal("0.52")


def test_apply_delta_size_update() -> None:
    state = _BookState()
    state.apply_snapshot({"bids": [{"price": "0.49", "size": "100"}], "asks": []})
    state.apply_delta({"changes": [{"side": "BUY", "price": "0.49", "size": "150"}]})
    snap = state.snapshot("t")
    assert snap.best_bid().size == Decimal("150")


def test_tracker_handle_event_emits_update() -> None:
    tracker = OrderBookTracker()
    tracker.subscribe_token("tok-1")
    received: list[BookUpdate] = []
    tracker.add_listener(lambda u: received.append(u))

    tracker._handle_message(
        json.dumps(
            {
                "event_type": "book",
                "asset_id": "tok-1",
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.51", "size": "100"}],
            }
        )
    )
    assert len(received) == 1
    assert received[0].token_id == "tok-1"
    assert received[0].snapshot.best_bid().price == Decimal("0.49")


def test_tracker_handles_array_payload() -> None:
    tracker = OrderBookTracker()
    tracker.subscribe_token("tok-1")
    received: list[BookUpdate] = []
    tracker.add_listener(lambda u: received.append(u))

    tracker._handle_message(
        json.dumps(
            [
                {
                    "event_type": "book",
                    "asset_id": "tok-1",
                    "bids": [{"price": "0.4", "size": "10"}],
                    "asks": [{"price": "0.6", "size": "10"}],
                },
                {
                    "event_type": "price_change",
                    "asset_id": "tok-1",
                    "changes": [{"side": "BUY", "price": "0.4", "size": "20"}],
                },
            ]
        )
    )
    assert len(received) == 2
    assert received[-1].snapshot.best_bid().size == Decimal("20")


def test_tracker_ignores_unsubscribed_or_malformed() -> None:
    tracker = OrderBookTracker()
    tracker.subscribe_token("tok-1")
    received: list[BookUpdate] = []
    tracker.add_listener(lambda u: received.append(u))

    # malformed JSON
    tracker._handle_message("not-json")
    # no asset_id
    tracker._handle_message(json.dumps({"event_type": "book"}))
    # unknown event type
    tracker._handle_message(json.dumps({"event_type": "weird", "asset_id": "tok-1"}))
    assert received == []
