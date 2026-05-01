"""Phase 6 — Risk guard: all 7 circuit breakers + JSON snapshot persistence.

Hard rules that no strategy can bypass. Every potential entry must pass
`guard.allow_entry()` before an order is placed.

The 7 circuit breakers:
    1. session_loss      — cumulative session PnL below MAX_SESSION_LOSS
    2. daily_loss        — cumulative daily PnL below MAX_DAILY_LOSS
    3. consecutive_loss  — N consecutive losing trades >= CONSECUTIVE_LOSS_LIMIT
    4. per_trade_size    — requested size exceeds MAX_PER_TRADE_USD
    5. health_gate       — ticker divergence / staleness (from TickerTracker)
    6. event_filter      — economic calendar blackout window
    7. confidence_gate   — signal confidence below MIN_SIGNAL_CONFIDENCE

Persistence: state is snapshotted to `state/guard_state.json` on every
outcome update so a crash recovery can reload without double-counting P&L.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from config.settings import Settings
from polybot.obs.logger import log


@dataclass
class GuardState:
    """Persisted counters."""
    consecutive_losses: int = 0
    session_pnl_usd: str = "0"   # str to survive JSON round-trip of Decimal
    daily_pnl_usd: str = "0"

    @property
    def session_pnl(self) -> Decimal:
        return Decimal(self.session_pnl_usd)

    @property
    def daily_pnl(self) -> Decimal:
        return Decimal(self.daily_pnl_usd)


_DEFAULT_SNAPSHOT = Path("state/guard_state.json")


class RiskGuard:
    """Centralised circuit-breaker layer.

    Usage::

        guard = RiskGuard(settings)
        allowed, reason = guard.allow_entry(size_usd, signal_confidence)
        if not allowed:
            return
        # ... place order ...
        guard.record_outcome(won=True, pnl_usd=Decimal("2.50"))
    """

    def __init__(
        self,
        settings: Settings,
        *,
        snapshot_path: Path | str = _DEFAULT_SNAPSHOT,
    ) -> None:
        self._cfg = settings
        self._snap = Path(snapshot_path)
        self._state = self._load_or_default()

    # ------------------------------------------------------------------
    # Primary gate
    # ------------------------------------------------------------------

    def allow_entry(
        self,
        size_usd: Decimal,
        signal_confidence: Decimal,
        *,
        health_ok: bool = True,
        event_blocked: bool = False,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). Call before every order placement."""
        s = self._state
        cfg = self._cfg

        # 1. Session loss
        if -s.session_pnl >= cfg.MAX_SESSION_LOSS:
            return False, f"session_loss=${-s.session_pnl:.2f} >= ${cfg.MAX_SESSION_LOSS}"

        # 2. Daily loss
        if -s.daily_pnl >= cfg.MAX_DAILY_LOSS:
            return False, f"daily_loss=${-s.daily_pnl:.2f} >= ${cfg.MAX_DAILY_LOSS}"

        # 3. Consecutive losses
        if s.consecutive_losses >= cfg.CONSECUTIVE_LOSS_LIMIT:
            return False, f"consecutive_losses={s.consecutive_losses} >= {cfg.CONSECUTIVE_LOSS_LIMIT}"

        # 4. Per-trade size
        if size_usd > cfg.MAX_PER_TRADE_USD:
            return False, f"size_usd={size_usd} > MAX_PER_TRADE_USD={cfg.MAX_PER_TRADE_USD}"

        # 5. Health gate
        if not health_ok:
            return False, "health_gate: ticker divergence or staleness"

        # 6. Event filter
        if event_blocked:
            return False, "event_filter: economic calendar blackout"

        # 7. Confidence gate
        if signal_confidence < cfg.MIN_SIGNAL_CONFIDENCE:
            return False, f"confidence={signal_confidence:.4f} < {cfg.MIN_SIGNAL_CONFIDENCE}"

        return True, ""

    # ------------------------------------------------------------------
    # Outcome recording + persistence
    # ------------------------------------------------------------------

    def record_outcome(self, *, won: bool, pnl_usd: Decimal) -> None:
        """Update counters and snapshot state to disk."""
        s = self._state
        new_session = s.session_pnl + pnl_usd
        new_daily = s.daily_pnl + pnl_usd
        new_consec = 0 if won else s.consecutive_losses + 1
        self._state = GuardState(
            consecutive_losses=new_consec,
            session_pnl_usd=str(new_session),
            daily_pnl_usd=str(new_daily),
        )
        self._save()

    def reset_daily(self) -> None:
        """Reset daily PnL counter at midnight UTC."""
        self._state = GuardState(
            consecutive_losses=self._state.consecutive_losses,
            session_pnl_usd=self._state.session_pnl_usd,
            daily_pnl_usd="0",
        )
        self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        try:
            self._snap.parent.mkdir(parents=True, exist_ok=True)
            self._snap.write_text(
                json.dumps(asdict(self._state), indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log.warning("guard: snapshot save failed: {}", exc)

    def _load_or_default(self) -> GuardState:
        if self._snap.exists():
            try:
                raw = json.loads(self._snap.read_text(encoding="utf-8"))
                return GuardState(**raw)
            except Exception as exc:
                log.warning("guard: snapshot load failed (using defaults): {}", exc)
        return GuardState()

    @property
    def state(self) -> GuardState:
        return self._state
