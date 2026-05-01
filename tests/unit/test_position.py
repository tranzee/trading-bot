"""Unit tests for SlotPosition (Phase 4)."""

from decimal import Decimal

import pytest

from polybot.engine.position import PositionStatus, SlotPosition


def _pos(**kw) -> SlotPosition:
    defaults = dict(
        direction="DOWN",
        token_id="tok_abc",
        shares=Decimal("10.20"),
        entry_price=Decimal("0.49"),
        slot_end_ms=1_700_000_300_000,
        paper=True,
    )
    defaults.update(kw)
    return SlotPosition(**defaults)


class TestPositionLifecycle:
    def test_initial_status_pending(self):
        assert _pos().status is PositionStatus.PENDING

    def test_record_fill(self):
        p = _pos()
        p.record_fill(Decimal("0.49"))
        assert p.status is PositionStatus.FILLED
        assert p.fill_price == Decimal("0.49")

    def test_cancel(self):
        p = _pos()
        p.cancel()
        assert p.status is PositionStatus.CANCELLED

    def test_record_settlement_win(self):
        p = _pos(shares=Decimal("10"), entry_price=Decimal("0.49"))
        p.record_fill(Decimal("0.49"))
        p.record_settlement(won=True, maker_rebate_usd=Decimal("0.005"))
        assert p.status is PositionStatus.SETTLED
        assert p.settled_win is True
        # gross = 10 * (1.00 - 0.49) = 5.10
        assert p.gross_pnl_usd == Decimal("5.10")
        # net = 5.10 + 0.005 = 5.105
        assert p.net_pnl_usd == Decimal("5.105")

    def test_record_settlement_loss(self):
        p = _pos(shares=Decimal("10"), entry_price=Decimal("0.49"))
        p.record_fill(Decimal("0.49"))
        p.record_settlement(won=False, maker_rebate_usd=Decimal("0.005"))
        assert p.status is PositionStatus.SETTLED
        assert p.settled_win is False
        # gross = -(10 * 0.49) = -4.90
        assert p.gross_pnl_usd == Decimal("-4.90")
        # net = -4.90 + 0.005 = -4.895
        assert p.net_pnl_usd == Decimal("-4.895")

    def test_up_direction_settlement_win(self):
        p = _pos(direction="UP", entry_price=Decimal("0.51"), shares=Decimal("10"))
        p.record_fill(Decimal("0.51"))
        p.record_settlement(won=True)
        # gross = 10 * (1.00 - 0.51) = 4.90
        assert p.gross_pnl_usd == Decimal("4.90")

    def test_no_rebate_default(self):
        p = _pos(shares=Decimal("5"), entry_price=Decimal("0.49"))
        p.record_fill(Decimal("0.49"))
        p.record_settlement(won=True)
        assert p.net_pnl_usd == p.gross_pnl_usd


_FAKE_ENV = {
    "POLYMARKET_PRIVATE_KEY": "0x" + "11" * 32,
    "POLYMARKET_FUNDER_ADDRESS": "0x" + "ab" * 20,
}


def _make_loop():
    from unittest.mock import MagicMock
    from config.settings import Settings
    from polybot.engine.slot_loop import SlotLoop
    from polybot.signal.engine import PriceActionEngine

    settings = Settings(_env_file=None, **_FAKE_ENV)  # type: ignore[arg-type]
    return SlotLoop(settings=settings, poly=MagicMock(), engine=PriceActionEngine(), paper=True)


class TestSlotLoopStats:
    """Stats property on SlotLoop (Phase 4)."""

    def test_stats_empty(self):
        loop = _make_loop()
        s = loop.stats
        assert s["slots_processed"] == 0
        assert s["signals_emitted"] == 0
        assert s["positions_opened"] == 0
        assert s["net_pnl_usd"] == "0"

    def test_stats_with_settled_positions(self):
        loop = _make_loop()

        # Inject two settled positions manually
        p1 = _pos(shares=Decimal("10"), entry_price=Decimal("0.49"))
        p1.record_fill(Decimal("0.49"))
        p1.record_settlement(won=True, maker_rebate_usd=Decimal("0.005"))

        p2 = _pos(shares=Decimal("10"), entry_price=Decimal("0.49"))
        p2.record_fill(Decimal("0.49"))
        p2.record_settlement(won=False, maker_rebate_usd=Decimal("0.005"))

        loop._positions = [p1, p2]
        loop._slot_count = 2
        loop._signal_count = 2

        s = loop.stats
        assert s["slots_processed"] == 2
        assert s["positions_opened"] == 2
        assert s["positions_settled"] == 2
        assert s["win_count"] == 1
        # net = 5.105 + (-4.895) = 0.21
        assert s["net_pnl_usd"] == "0.210"
