"""Phase 5 — fractional Kelly position sizer with hard risk caps.

Kelly fraction:  f* = p - q/b
    where b = (1 - entry_price) / entry_price   (net odds)
          p = win probability (continuation_prior from the signal)
          q = 1 - p

Sizing pipeline (each step can only decrease the bet):
    1. Full Kelly fraction of bankroll
    2. Scale by KELLY_FRACTION_MULTIPLIER (quarter-Kelly by default)
    3. Clamp to MAX_PER_TRADE_FRACTION of bankroll
    4. Clamp to MAX_PER_TRADE_USD hard cap
    5. Return Decimal("0") if any circuit breaker is open

Circuit breakers (non-resettable within a session):
    - consecutive_losses >= CONSECUTIVE_LOSS_LIMIT
    - session loss >= MAX_SESSION_LOSS
    - daily loss >= MAX_DAILY_LOSS
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from config.settings import Settings


@dataclass
class SizerState:
    """Mutable runtime counters tracked across trades."""
    consecutive_losses: int = 0
    session_pnl_usd: Decimal = Decimal("0")
    daily_pnl_usd: Decimal = Decimal("0")


class PositionSizer:
    """Kelly-based position sizer with session/daily circuit breakers.

    Usage::

        sizer = PositionSizer(settings)
        size = sizer.compute_size_usd(win_prob=Decimal("0.56"), entry_price=Decimal("0.49"))
        # ... trade executes ...
        sizer.record_outcome(won=True, pnl_usd=Decimal("2.55"))
    """

    # Quarter-Kelly is standard for high-variance, small-sample regimes.
    KELLY_FRACTION_MULTIPLIER: Decimal = Decimal("0.25")

    def __init__(
        self,
        settings: Settings,
        *,
        state: SizerState | None = None,
    ) -> None:
        self._settings = settings
        self._state = state or SizerState()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def compute_size_usd(
        self,
        win_prob: Decimal,
        entry_price: Decimal,
    ) -> Decimal:
        """Return the bet size in USD after Kelly + caps + circuit breakers.

        Returns Decimal("0") when any circuit breaker is open or when Kelly
        sizing produces a non-positive stake (edge below breakeven).
        """
        blocked, _ = self.is_blocked()
        if blocked:
            return Decimal("0")

        # b = net profit per unit staked when winning
        #   = (1 - entry_price) / entry_price
        if entry_price <= 0 or entry_price >= 1:
            return Decimal("0")
        b = (Decimal("1") - entry_price) / entry_price
        q = Decimal("1") - win_prob

        kelly_f = max(Decimal("0"), win_prob - q / b)
        if kelly_f == 0:
            return Decimal("0")

        scaled_f = kelly_f * self.KELLY_FRACTION_MULTIPLIER
        bankroll = self._settings.WALLET_BALANCE

        size = min(
            scaled_f * bankroll,
            bankroll * self._settings.MAX_PER_TRADE_FRACTION,
            self._settings.MAX_PER_TRADE_USD,
        )
        return max(Decimal("0"), size.quantize(Decimal("0.01")))

    def record_outcome(self, *, won: bool, pnl_usd: Decimal) -> None:
        """Update running tallies after a trade settles."""
        self._state.session_pnl_usd += pnl_usd
        self._state.daily_pnl_usd += pnl_usd
        if won:
            self._state.consecutive_losses = 0
        else:
            self._state.consecutive_losses += 1

    def is_blocked(self) -> tuple[bool, str]:
        """Return (blocked, reason). blocked=True → no new positions."""
        s = self._state
        cfg = self._settings
        if s.consecutive_losses >= cfg.CONSECUTIVE_LOSS_LIMIT:
            return True, f"consecutive_losses={s.consecutive_losses} >= {cfg.CONSECUTIVE_LOSS_LIMIT}"
        session_loss = -s.session_pnl_usd
        if session_loss >= cfg.MAX_SESSION_LOSS:
            return True, f"session_loss=${session_loss:.2f} >= ${cfg.MAX_SESSION_LOSS}"
        daily_loss = -s.daily_pnl_usd
        if daily_loss >= cfg.MAX_DAILY_LOSS:
            return True, f"daily_loss=${daily_loss:.2f} >= ${cfg.MAX_DAILY_LOSS}"
        return False, ""

    def reset_daily(self) -> None:
        """Reset daily P&L counter (call at midnight UTC)."""
        self._state.daily_pnl_usd = Decimal("0")

    @property
    def state(self) -> SizerState:
        return self._state
