"""EPA / IPA efficiency state machine (§6.2.3).

States:
    EPA_C2     — first TLQ break in trend; only this one TLQ is "active."
    EPA_C1     — TLQ has been touched on pullback (efficient).
    EFFICIENT  — generic efficient state (after EPA C1 transition).
    IPA_FROZEN — 2 consecutive TLQ breaks WITHOUT any pullback touch.

Transitions:
    NEW_TREND -> EPA_C2 on first TLQ break
    EPA_C2 -> EPA_C1 (on TLQ touch) -> EFFICIENT (on next TLQ break)
    EFFICIENT -> IPA_FROZEN (on 2 consecutive misses)
    IPA_FROZEN -> EFFICIENT (on pullback that touches an unfilled TLQ)
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from polybot.signal.models import EfficiencyState, LiquidityNode


class EfficiencyTracker:
    """Stateful EPA/IPA tracker. Caller drives transitions via `on_*` methods."""

    def __init__(self, *, ipa_consecutive_miss_threshold: int = 2) -> None:
        self._state: EfficiencyState = EfficiencyState.EFFICIENT
        self._unfilled_tlqs: list[LiquidityNode] = []
        self._consecutive_misses: int = 0
        self._miss_threshold = ipa_consecutive_miss_threshold
        self._first_break_seen = False

    @property
    def state(self) -> EfficiencyState:
        return self._state

    @property
    def consecutive_misses(self) -> int:
        return self._consecutive_misses

    @property
    def unfilled_tlqs(self) -> list[LiquidityNode]:
        return list(self._unfilled_tlqs)

    def on_tlq_break(
        self,
        broken_tlq: LiquidityNode,
        *,
        tlq_was_touched_during_pullback: bool,
    ) -> None:
        """Caller signals: a TLQ break has just happened.

        - If the previous unfilled TLQ wasn't touched on pullback before this break:
          increment consecutive_misses.
        - If misses >= threshold: enter IPA_FROZEN.
        """
        if not self._first_break_seen:
            # First break — enter EPA C2 (the conditions section says C2 is
            # "first TLQ break"; we count the unfilled set).
            self._state = EfficiencyState.EPA_C2
            self._first_break_seen = True
            self._unfilled_tlqs.append(broken_tlq)
            return

        if tlq_was_touched_during_pullback:
            # Reset miss streak: the previous TLQ was filled.
            self._consecutive_misses = 0
            # Move to EFFICIENT on the SECOND break that maintained efficiency.
            if self._state in (EfficiencyState.EPA_C2, EfficiencyState.EPA_C1):
                self._state = EfficiencyState.EFFICIENT
        else:
            # Previous TLQ unfilled: this is a miss.
            self._consecutive_misses += 1
            if self._consecutive_misses >= self._miss_threshold:
                self._state = EfficiencyState.IPA_FROZEN

        self._unfilled_tlqs.append(broken_tlq)

    def on_pullback_touch(self, price: Decimal) -> bool:
        """A pullback touched `price`. If it matches an unfilled TLQ, clear it
        and (if we were frozen) exit IPA back to EFFICIENT.

        Returns True if a TLQ was cleared.
        """
        cleared_any = False
        for node in list(self._unfilled_tlqs):
            # Down-trend TLQs are LOWs; pullback touches them from below moving up.
            # Use exact match on the node price; the touch detection happens
            # upstream (signal/math.py.touched).
            if node.swept_at_ms is None and node.bridge_filled_at_ms is None:
                if node.price == price:
                    node.bridge_filled_at_ms = 0  # marker — operator never sees this
                    self._unfilled_tlqs.remove(node)
                    cleared_any = True
        if cleared_any and self._state is EfficiencyState.IPA_FROZEN:
            self._state = EfficiencyState.EFFICIENT
            self._consecutive_misses = 0
        elif cleared_any:
            self._consecutive_misses = max(0, self._consecutive_misses - 1)
        return cleared_any

    def is_signal_allowed(self) -> bool:
        """Public gate the strategy queries. IPA_FROZEN suspends signals."""
        return self._state is not EfficiencyState.IPA_FROZEN

    def feed_breaks(
        self,
        breaks: Iterable[tuple[LiquidityNode, bool]],
    ) -> None:
        """Convenience: feed a sequence of (broken_tlq, was_touched) pairs."""
        for tlq, touched in breaks:
            self.on_tlq_break(tlq, tlq_was_touched_during_pullback=touched)
