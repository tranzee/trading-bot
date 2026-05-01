"""Phase 1 acceptance — live order book end-to-end.

Skipped by default; run with `pytest -m integration` when on a host with
internet + valid POLYMARKET_PRIVATE_KEY in .env.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

ALLOW_LIVE = os.environ.get("POLYBOT_ALLOW_LIVE_TESTS") == "1"


@pytest.mark.skipif(not ALLOW_LIVE, reason="set POLYBOT_ALLOW_LIVE_TESTS=1 to enable")
@pytest.mark.asyncio
async def test_live_orderbook_60s_two_sided() -> None:
    """Phase 1 acceptance: connect for 60s, both sides have liquidity at end."""
    from polybot.poly.live_view import run_live_orderbook

    rc = await run_live_orderbook(duration_s=60, slot_offset=0)
    assert rc == 0, f"live_orderbook returned {rc} (expected 0 = both sides liquid)"
