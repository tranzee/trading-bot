"""Slot-arithmetic and slug-derivation tests for poly/market_discovery."""

from __future__ import annotations

from polybot.poly.market_discovery import (
    _extract_condition_id,
    _extract_token_ids,
    slot_boundary_ms,
    slug_for_slot,
)


def test_slot_boundary_aligns_to_5min_grid() -> None:
    # 2026-04-26T01:23:45Z -> next 5min boundary is 01:25:00Z
    # = 1745630700000 + ((1745631825000 - 1745630700000) ... let's just check the math holds
    now_ms = 1_777_166_625_000  # arbitrary
    end = slot_boundary_ms(now_ms)
    assert end % 300_000 == 0
    assert end > now_ms
    assert end - now_ms <= 300_000


def test_slot_boundary_at_exact_grid() -> None:
    # Exactly on a boundary: should return next boundary, not current
    boundary = 1_777_167_000_000  # divisible by 300_000
    assert boundary % 300_000 == 0
    end = slot_boundary_ms(boundary)
    assert end == boundary + 300_000


def test_slug_format() -> None:
    assert slug_for_slot(1_777_167_000_000) == "btc-updown-5m-1777167000"


def test_extract_token_ids_from_event() -> None:
    event = {
        "markets": [
            {
                "conditionId": "0xabc",
                "clobTokenIds": '["111", "222"]',
            }
        ]
    }
    up, down = _extract_token_ids(event)
    assert up == "111"
    assert down == "222"
    assert _extract_condition_id(event) == "0xabc"


def test_extract_token_ids_already_parsed() -> None:
    event = {"markets": [{"clobTokenIds": ["aaa", "bbb"], "conditionId": "0xfff"}]}
    assert _extract_token_ids(event) == ("aaa", "bbb")
    assert _extract_condition_id(event) == "0xfff"
