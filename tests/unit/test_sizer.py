"""Unit tests for PositionSizer (Phase 5)."""

from decimal import Decimal

import pytest

from config.settings import Settings
from polybot.risk.sizer import PositionSizer, SizerState

_FAKE_ENV = {
    "POLYMARKET_PRIVATE_KEY": "0x" + "11" * 32,
    "POLYMARKET_FUNDER_ADDRESS": "0x" + "ab" * 20,
}


def _settings(**overrides) -> Settings:
    env = {**_FAKE_ENV, **overrides}
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def _sizer(**overrides) -> PositionSizer:
    return PositionSizer(_settings(**overrides))


class TestKellyFormula:
    def test_positive_edge_returns_nonzero(self):
        sz = _sizer()
        size = sz.compute_size_usd(win_prob=Decimal("0.56"), entry_price=Decimal("0.49"))
        assert size > 0

    def test_size_capped_by_max_per_trade_usd(self):
        # Default MAX_PER_TRADE_USD = 5.00
        sz = _sizer()
        # Very high win prob → Kelly wants big bet; cap kicks in
        size = sz.compute_size_usd(win_prob=Decimal("0.90"), entry_price=Decimal("0.49"))
        assert size <= Decimal("5.00")

    def test_size_capped_by_fraction(self):
        # WALLET_BALANCE=100, MAX_PER_TRADE_FRACTION=0.05 → $5 cap matches MAX_PER_TRADE_USD
        sz = _sizer()
        size = sz.compute_size_usd(win_prob=Decimal("0.90"), entry_price=Decimal("0.49"))
        assert size <= Decimal("100") * Decimal("0.05")

    def test_below_breakeven_returns_zero(self):
        # At p=0.30 with entry=0.49, Kelly is negative → 0
        sz = _sizer()
        size = sz.compute_size_usd(win_prob=Decimal("0.30"), entry_price=Decimal("0.49"))
        assert size == Decimal("0")

    def test_exact_breakeven_zero(self):
        # Breakeven: p - q/b = 0 → p = 1/(1+b) = entry_price
        sz = _sizer()
        size = sz.compute_size_usd(win_prob=Decimal("0.49"), entry_price=Decimal("0.49"))
        assert size == Decimal("0")

    def test_quarter_kelly_scaling(self):
        # Kelly for p=0.60, entry=0.49:
        # b = 0.51/0.49 ≈ 1.04082; f* = 0.60 - 0.40/1.04082 ≈ 0.6 - 0.384 ≈ 0.216
        # quarter = 0.054; * 100 = $5.4 → capped at $5.00
        sz = _sizer()
        size = sz.compute_size_usd(win_prob=Decimal("0.60"), entry_price=Decimal("0.49"))
        assert size == Decimal("5.00")  # capped by MAX_PER_TRADE_USD

    def test_returns_zero_on_invalid_entry_price(self):
        sz = _sizer()
        assert sz.compute_size_usd(win_prob=Decimal("0.60"), entry_price=Decimal("0")) == 0
        assert sz.compute_size_usd(win_prob=Decimal("0.60"), entry_price=Decimal("1")) == 0


class TestCircuitBreakers:
    def test_consecutive_loss_blocks(self):
        sz = _sizer()
        # Default CONSECUTIVE_LOSS_LIMIT = 5
        for _ in range(5):
            sz.record_outcome(won=False, pnl_usd=Decimal("-2"))
        blocked, reason = sz.is_blocked()
        assert blocked
        assert "consecutive" in reason
        assert sz.compute_size_usd(Decimal("0.60"), Decimal("0.49")) == 0

    def test_win_resets_consecutive_counter(self):
        sz = _sizer()
        for _ in range(3):
            sz.record_outcome(won=False, pnl_usd=Decimal("-2"))
        sz.record_outcome(won=True, pnl_usd=Decimal("2"))
        assert sz.state.consecutive_losses == 0
        blocked, _ = sz.is_blocked()
        assert not blocked

    def test_session_loss_blocks(self):
        sz = _sizer()
        # MAX_SESSION_LOSS = 10.0
        sz.record_outcome(won=False, pnl_usd=Decimal("-10"))
        blocked, reason = sz.is_blocked()
        assert blocked
        assert "session_loss" in reason

    def test_daily_loss_blocks(self):
        # Set session limit high so only daily fires
        sz = _sizer(MAX_SESSION_LOSS="30")
        # MAX_DAILY_LOSS default = 20.0
        sz.record_outcome(won=False, pnl_usd=Decimal("-20"))
        blocked, reason = sz.is_blocked()
        assert blocked
        assert "daily_loss" in reason

    def test_reset_daily_clears_daily_counter(self):
        sz = _sizer()
        sz.record_outcome(won=False, pnl_usd=Decimal("-20"))
        sz.reset_daily()
        blocked, _ = sz.is_blocked()
        # Session loss still accumulates even after daily reset
        assert blocked  # session_loss still -20 >= 10

    def test_profitable_trades_not_blocked(self):
        sz = _sizer()
        for _ in range(10):
            sz.record_outcome(won=True, pnl_usd=Decimal("2"))
        blocked, _ = sz.is_blocked()
        assert not blocked
