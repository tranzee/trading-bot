"""4-tier liquidity hierarchy (§6.2.2).

For DOWNTREND (uptrend mirrors):
    MAIN HIGH:  the absolute peak since trend genesis. Static once set.
                Reset only on macro reversal.
    SLQ:        the first lower high formed AFTER MAIN that breaks the most
                recent low (TLQ). Static once set.
    TLQ:        dynamic — each new swing low becomes the active TLQ.
    ILQ:        dynamic — set to the lower high that immediately precedes
                each new TLQ break.

The hierarchy consumes ONLY confirmed pivots (per §1.5.2).
"""

from __future__ import annotations

from collections.abc import Iterable

from polybot.signal.models import (
    LiquidityNode,
    NodeType,
    Pivot,
    PivotType,
    Trend,
)


class LiquidityHierarchy:
    """Stateful 4-tier hierarchy. Construct with a known trend direction.

    Public:
        update(new_pivot)                 -> None
        register_tlq_break(at_ts_ms)      -> None  # called when a candle closes through TLQ
        current_main_high() / current_main_low()
        current_slq() / current_tlq() / current_ilq()
        mark_swept(node_id, ts_ms)
        snapshot() -> dict (for logging)
        confirmed_tlq_breaks_since_slq    -> int
    """

    def __init__(self, direction: Trend) -> None:
        if direction not in (Trend.DOWN, Trend.UP):
            raise ValueError("LiquidityHierarchy direction must be DOWN or UP")
        self._direction = direction
        self._main_high: LiquidityNode | None = None
        self._main_low: LiquidityNode | None = None
        self._slq: LiquidityNode | None = None
        self._tlq: LiquidityNode | None = None
        self._ilq: LiquidityNode | None = None
        # Track most-recently-confirmed lower-high (or higher-low for uptrend).
        # Used to set ILQ on each TLQ break.
        self._latest_against_pivot: LiquidityNode | None = None
        self._tlq_breaks_since_slq: int = 0
        # All swings ever ingested (for debugging / chart overlay).
        self._all_pivots: list[Pivot] = []

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def seed_main(self, anchor: Pivot) -> None:
        """Set MAIN HIGH (DOWN) or MAIN LOW (UP) from the cold-start anchor."""
        node = LiquidityNode(
            price=anchor.price,
            timestamp_ms=anchor.timestamp_ms,
            node_type=NodeType.MAIN,
            is_static=True,
            direction=anchor.type,
            formed_at_pivot_index=anchor.index,
        )
        if self._direction is Trend.DOWN:
            if anchor.type is not PivotType.HIGH:
                raise ValueError("DOWN trend MAIN must seed from a HIGH pivot")
            self._main_high = node
        else:
            if anchor.type is not PivotType.LOW:
                raise ValueError("UP trend MAIN must seed from a LOW pivot")
            self._main_low = node

    def seed_secondary_extreme(self, pivot: Pivot) -> None:
        """Optional: record the OTHER extreme (low for DOWN trend) for invalidation."""
        node = LiquidityNode(
            price=pivot.price,
            timestamp_ms=pivot.timestamp_ms,
            node_type=NodeType.MAIN,  # tracked as MAIN-LOW (or MAIN-HIGH for UP trend)
            is_static=True,
            direction=pivot.type,
            formed_at_pivot_index=pivot.index,
        )
        if self._direction is Trend.DOWN and pivot.type is PivotType.LOW:
            self._main_low = node
        elif self._direction is Trend.UP and pivot.type is PivotType.HIGH:
            self._main_high = node

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, pivot: Pivot) -> None:
        """Incorporate a new CONFIRMED pivot. Caller is responsible for
        feeding only confirmed pivots (per §1.5.2)."""
        if not pivot.is_confirmed:
            raise ValueError("LiquidityHierarchy.update only accepts confirmed pivots")
        self._all_pivots.append(pivot)
        if self._direction is Trend.DOWN:
            self._update_down(pivot)
        else:
            self._update_up(pivot)

    def _update_down(self, pivot: Pivot) -> None:
        """Downtrend update rules.

        - HIGH pivot: candidate for ILQ (lower high). Update _latest_against_pivot.
        - LOW pivot: becomes the new TLQ; if no SLQ yet, this LOW also breaks
          the previous TLQ which qualifies the most recent HIGH as SLQ.
        """
        # MAIN HIGH never updates after initial set in a downtrend.
        # (Verified by the gating Hypothesis property test.)

        if pivot.type is PivotType.HIGH:
            # In a downtrend, we expect lower highs. We don't enforce that here —
            # the MAIN HIGH check below will flag a higher high.
            self._latest_against_pivot = LiquidityNode(
                price=pivot.price,
                timestamp_ms=pivot.timestamp_ms,
                node_type=NodeType.ILQ,
                is_static=False,
                direction=PivotType.HIGH,
                formed_at_pivot_index=pivot.index,
            )
            # If this high exceeds Main High, the macro context has reversed —
            # caller (invalidation.py) handles that.
            return

        # LOW pivot
        prev_tlq = self._tlq
        new_tlq = LiquidityNode(
            price=pivot.price,
            timestamp_ms=pivot.timestamp_ms,
            node_type=NodeType.TLQ,
            is_static=False,
            direction=PivotType.LOW,
            formed_at_pivot_index=pivot.index,
        )

        if prev_tlq is not None and pivot.price < prev_tlq.price:
            # New low broke the previous TLQ -> count a break and update ILQ.
            self._tlq_breaks_since_slq += 1
            if self._latest_against_pivot is not None:
                self._ilq = LiquidityNode(
                    price=self._latest_against_pivot.price,
                    timestamp_ms=self._latest_against_pivot.timestamp_ms,
                    node_type=NodeType.ILQ,
                    is_static=False,
                    direction=PivotType.HIGH,
                    formed_at_pivot_index=self._latest_against_pivot.formed_at_pivot_index,
                )
            # First TLQ break promotes the prior latest_against_pivot to SLQ.
            if self._slq is None and self._latest_against_pivot is not None:
                self._slq = LiquidityNode(
                    price=self._latest_against_pivot.price,
                    timestamp_ms=self._latest_against_pivot.timestamp_ms,
                    node_type=NodeType.SLQ,
                    is_static=True,
                    direction=PivotType.HIGH,
                    formed_at_pivot_index=self._latest_against_pivot.formed_at_pivot_index,
                )

        self._tlq = new_tlq

    def _update_up(self, pivot: Pivot) -> None:
        """Uptrend mirror of _update_down."""
        if pivot.type is PivotType.LOW:
            self._latest_against_pivot = LiquidityNode(
                price=pivot.price,
                timestamp_ms=pivot.timestamp_ms,
                node_type=NodeType.ILQ,
                is_static=False,
                direction=PivotType.LOW,
                formed_at_pivot_index=pivot.index,
            )
            return

        # HIGH pivot
        prev_tlq = self._tlq
        new_tlq = LiquidityNode(
            price=pivot.price,
            timestamp_ms=pivot.timestamp_ms,
            node_type=NodeType.TLQ,
            is_static=False,
            direction=PivotType.HIGH,
            formed_at_pivot_index=pivot.index,
        )

        if prev_tlq is not None and pivot.price > prev_tlq.price:
            self._tlq_breaks_since_slq += 1
            if self._latest_against_pivot is not None:
                self._ilq = LiquidityNode(
                    price=self._latest_against_pivot.price,
                    timestamp_ms=self._latest_against_pivot.timestamp_ms,
                    node_type=NodeType.ILQ,
                    is_static=False,
                    direction=PivotType.LOW,
                    formed_at_pivot_index=self._latest_against_pivot.formed_at_pivot_index,
                )
            if self._slq is None and self._latest_against_pivot is not None:
                self._slq = LiquidityNode(
                    price=self._latest_against_pivot.price,
                    timestamp_ms=self._latest_against_pivot.timestamp_ms,
                    node_type=NodeType.SLQ,
                    is_static=True,
                    direction=PivotType.LOW,
                    formed_at_pivot_index=self._latest_against_pivot.formed_at_pivot_index,
                )

        self._tlq = new_tlq

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def current_main_high(self) -> LiquidityNode | None:
        return self._main_high

    def current_main_low(self) -> LiquidityNode | None:
        return self._main_low

    def current_slq(self) -> LiquidityNode | None:
        return self._slq

    def current_tlq(self) -> LiquidityNode | None:
        return self._tlq

    def current_ilq(self) -> LiquidityNode | None:
        return self._ilq

    @property
    def confirmed_tlq_breaks_since_slq(self) -> int:
        return self._tlq_breaks_since_slq

    @property
    def direction(self) -> Trend:
        return self._direction

    def mark_swept(self, node_id: str, ts_ms: int) -> None:
        for node in (self._main_high, self._main_low, self._slq, self._tlq, self._ilq):
            if node is not None and node.node_id == node_id:
                node.swept_at_ms = ts_ms
                return

    def snapshot(self) -> dict[str, object]:
        return {
            "direction": self._direction.value,
            "main_high": self._main_high.price if self._main_high else None,
            "main_low": self._main_low.price if self._main_low else None,
            "slq": self._slq.price if self._slq else None,
            "tlq": self._tlq.price if self._tlq else None,
            "ilq": self._ilq.price if self._ilq else None,
            "tlq_breaks_since_slq": self._tlq_breaks_since_slq,
        }

    def feed(self, pivots: Iterable[Pivot]) -> None:
        """Convenience: feed a sequence of confirmed pivots in order."""
        for p in pivots:
            self.update(p)
