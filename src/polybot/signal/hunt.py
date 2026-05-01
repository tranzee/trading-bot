"""Sweep detection + Efficiency Bridge (§6.2.4).

ILQ sweep validation:
    - If swept ILQ was formed when EPA was efficient ("pre-efficient ILQ"):
      INSTANT validation.
    - Otherwise: requires `EfficiencyBridge` — price must touch the unfilled
      TLQ between the swept ILQ and SLQ before the validation completes.

SLQ sweep validation: instant (no bridge).
"""

from __future__ import annotations

from decimal import Decimal

from polybot.signal import math as M
from polybot.signal.models import (
    EfficiencyStatus,
    LiquidityNode,
    NodeType,
    SweepEvent,
    SweepType,
    Trend,
)


class EfficiencyBridge:
    """Tracks whether the unfilled TLQ between a swept ILQ and the SLQ has
    been touched. Once touched, `is_completed()` returns True."""

    def __init__(self, target_tlq: LiquidityNode) -> None:
        self._target = target_tlq
        self._completed = False
        self._completed_at_ms: int | None = None

    def on_price(self, price: Decimal, ts_ms: int, *, basis_bps: int = 1) -> bool:
        """Feed a live price. Returns True if THIS call completed the bridge."""
        if self._completed:
            return False
        # Touch detection: did `price` reach the TLQ price within tolerance?
        # We treat 'price' as the candle's instantaneous reach; for closed
        # candles, the engine should call this twice (once with high, once
        # with low) to be conservative.
        # Use the math.touched predicate's spirit but with point price.
        within = abs(price - self._target.price) <= M.min_breach_distance(
            self._target.price, basis_bps
        )
        if within or price == self._target.price:
            self._completed = True
            self._completed_at_ms = ts_ms
            return True
        return False

    def is_completed(self) -> bool:
        return self._completed

    @property
    def completed_at_ms(self) -> int | None:
        return self._completed_at_ms

    @property
    def target_tlq(self) -> LiquidityNode:
        return self._target


class SweepDetector:
    """Detects ILQ and SLQ sweeps and emits SweepEvents.

    The caller feeds candle highs/lows. The detector maintains references to
    the current ILQ and SLQ (via setters) and decides whether each candle
    swept either.
    """

    def __init__(self, direction: Trend, *, basis_bps: int = 1) -> None:
        self._direction = direction
        self._basis_bps = basis_bps
        self._ilq: LiquidityNode | None = None
        self._slq: LiquidityNode | None = None
        self._tlq_for_bridge: LiquidityNode | None = None
        self._active_bridge: EfficiencyBridge | None = None

    def set_levels(
        self,
        *,
        ilq: LiquidityNode | None,
        slq: LiquidityNode | None,
        unfilled_tlq_for_bridge: LiquidityNode | None,
    ) -> None:
        self._ilq = ilq
        self._slq = slq
        self._tlq_for_bridge = unfilled_tlq_for_bridge

    def on_candle(
        self,
        *,
        ts_ms: int,
        high: Decimal,
        low: Decimal,
    ) -> tuple[SweepEvent | None, EfficiencyBridge | None]:
        """Process a candle. Returns (sweep_event_or_None, bridge_started_or_None)."""
        # Bridge progress (if active): pulse the bridge with both wick extremes.
        if self._active_bridge is not None and not self._active_bridge.is_completed():
            self._active_bridge.on_price(high, ts_ms, basis_bps=self._basis_bps)
            self._active_bridge.on_price(low, ts_ms, basis_bps=self._basis_bps)

        sweep: SweepEvent | None = None
        new_bridge: EfficiencyBridge | None = None

        # SLQ sweep (instant)
        if self._slq is not None and self._slq.swept_at_ms is None:
            if self._direction is Trend.DOWN and M.swept(
                self._slq.price, "above", high, basis_bps=self._basis_bps
            ):
                sweep = SweepEvent(
                    type=SweepType.SLQ,
                    swept_node_id=self._slq.node_id,
                    swept_at_ms=ts_ms,
                    swept_price=high,
                    validated_immediately=True,
                    requires_bridge=False,
                )
                self._slq.swept_at_ms = ts_ms
            elif self._direction is Trend.UP and M.swept(
                self._slq.price, "below", low, basis_bps=self._basis_bps
            ):
                sweep = SweepEvent(
                    type=SweepType.SLQ,
                    swept_node_id=self._slq.node_id,
                    swept_at_ms=ts_ms,
                    swept_price=low,
                    validated_immediately=True,
                    requires_bridge=False,
                )
                self._slq.swept_at_ms = ts_ms

        # ILQ sweep — if SLQ wasn't already swept this candle
        if (
            sweep is None
            and self._ilq is not None
            and self._ilq.swept_at_ms is None
        ):
            ilq_swept = False
            ilq_price_at_sweep = high
            if self._direction is Trend.DOWN and M.swept(
                self._ilq.price, "above", high, basis_bps=self._basis_bps
            ):
                ilq_swept = True
                ilq_price_at_sweep = high
            elif self._direction is Trend.UP and M.swept(
                self._ilq.price, "below", low, basis_bps=self._basis_bps
            ):
                ilq_swept = True
                ilq_price_at_sweep = low

            if ilq_swept:
                pre_efficient = self._ilq.efficiency_status is EfficiencyStatus.PRE_EFFICIENT
                requires_bridge = (not pre_efficient) and self._tlq_for_bridge is not None
                sweep = SweepEvent(
                    type=SweepType.ILQ,
                    swept_node_id=self._ilq.node_id,
                    swept_at_ms=ts_ms,
                    swept_price=ilq_price_at_sweep,
                    validated_immediately=not requires_bridge,
                    requires_bridge=requires_bridge,
                )
                self._ilq.swept_at_ms = ts_ms
                if requires_bridge and self._tlq_for_bridge is not None:
                    new_bridge = EfficiencyBridge(self._tlq_for_bridge)
                    self._active_bridge = new_bridge

        return sweep, new_bridge

    @property
    def active_bridge(self) -> EfficiencyBridge | None:
        return self._active_bridge

    def clear_bridge(self) -> None:
        self._active_bridge = None
