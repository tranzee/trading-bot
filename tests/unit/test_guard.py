"""Unit tests for RiskGuard (Phase 6)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from config.settings import Settings
from polybot.risk.guard import RiskGuard

_FAKE_ENV = {
    "POLYMARKET_PRIVATE_KEY": "0x" + "11" * 32,
    "POLYMARKET_FUNDER_ADDRESS": "0x" + "ab" * 20,
}


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **{**_FAKE_ENV, **kw})  # type: ignore[arg-type]


def _guard(tmp_path: Path, **kw) -> RiskGuard:
    return RiskGuard(_settings(**kw), snapshot_path=tmp_path / "guard.json")


class TestCircuitBreakers:
    def test_fresh_guard_allows_entry(self, tmp_path):
        g = _guard(tmp_path)
        ok, reason = g.allow_entry(Decimal("5"), Decimal("0.60"))
        assert ok
        assert reason == ""

    def test_confidence_gate(self, tmp_path):
        # MIN_SIGNAL_CONFIDENCE default = 0.55
        g = _guard(tmp_path)
        ok, reason = g.allow_entry(Decimal("5"), Decimal("0.40"))
        assert not ok
        assert "confidence" in reason

    def test_size_cap(self, tmp_path):
        g = _guard(tmp_path)  # MAX_PER_TRADE_USD = 5.0
        ok, reason = g.allow_entry(Decimal("10"), Decimal("0.60"))
        assert not ok
        assert "MAX_PER_TRADE_USD" in reason

    def test_health_gate(self, tmp_path):
        g = _guard(tmp_path)
        ok, reason = g.allow_entry(Decimal("5"), Decimal("0.60"), health_ok=False)
        assert not ok
        assert "health_gate" in reason

    def test_event_filter_gate(self, tmp_path):
        g = _guard(tmp_path)
        ok, reason = g.allow_entry(Decimal("5"), Decimal("0.60"), event_blocked=True)
        assert not ok
        assert "event_filter" in reason

    def test_session_loss_blocks(self, tmp_path):
        g = _guard(tmp_path)
        g.record_outcome(won=False, pnl_usd=Decimal("-10"))
        ok, reason = g.allow_entry(Decimal("5"), Decimal("0.60"))
        assert not ok
        assert "session_loss" in reason

    def test_consecutive_loss_blocks(self, tmp_path):
        g = _guard(tmp_path)
        for _ in range(5):
            g.record_outcome(won=False, pnl_usd=Decimal("-1"))
        ok, reason = g.allow_entry(Decimal("5"), Decimal("0.60"))
        assert not ok
        assert "consecutive" in reason


class TestPersistence:
    def test_state_saved_after_outcome(self, tmp_path):
        g = _guard(tmp_path)
        g.record_outcome(won=True, pnl_usd=Decimal("2.50"))
        snap = tmp_path / "guard.json"
        assert snap.exists()
        data = json.loads(snap.read_text())
        assert data["consecutive_losses"] == 0
        assert Decimal(data["session_pnl_usd"]) == Decimal("2.50")

    def test_state_reloaded_on_restart(self, tmp_path):
        g1 = _guard(tmp_path)
        g1.record_outcome(won=False, pnl_usd=Decimal("-3"))
        g1.record_outcome(won=False, pnl_usd=Decimal("-3"))

        g2 = _guard(tmp_path)  # reload from same snapshot
        assert g2.state.consecutive_losses == 2
        assert g2.state.session_pnl == Decimal("-6")

    def test_reset_daily(self, tmp_path):
        g = _guard(tmp_path)
        g.record_outcome(won=False, pnl_usd=Decimal("-5"))
        g.reset_daily()
        assert g.state.daily_pnl == Decimal("0")
        assert g.state.session_pnl == Decimal("-5")  # session unchanged
