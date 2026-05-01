"""Price action engine — the orchestrator.

Public surface:
    PriceActionEngine.bootstrap_from_history(candles)   # §6.2.9 cold-start
    PriceActionEngine.on_tick(tick)                     # streaming
    PriceActionEngine.on_candle_close(candle)           # may emit Signal
    PriceActionEngine.state                             # PAState snapshot

§6.2.10 alert sequence:
    Alert 1 (PRE_ALERT)      — sweep validated, zone scan begins.
    Alert 2 (EARLY_WARNING)  — UI-only at T+180s; never triggers orders.
    Alert 3 (EXECUTION)      — at T+300s on candle close, emits Signal.

Confidence formula (§6.2.8):
    confidence = continuation_prior(setup, depth_bucket)
               × zone.freshness_at(now)
               × htf_alignment_multiplier
               × volume_filter_multiplier
               × pattern_confidence_score
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from polybot.obs.logger import log
from polybot.signal import math as M
from polybot.signal.continuation_filter import (
    ContinuationFilterParams,
    evaluate as continuation_evaluate,
)
from polybot.signal.efficiency import EfficiencyTracker
from polybot.signal.htf_filter import HtfFilter
from polybot.signal.hunt import SweepDetector
from polybot.signal.invalidation import (
    InvalidationContext,
    first_invalidation,
)
from polybot.signal.liquidity import LiquidityHierarchy
from polybot.signal.models import (
    AlertPayload,
    AlertType,
    DepthBucket,
    EfficiencyState,
    InvalidationEvent,
    PAState,
    Pivot,
    PivotType,
    SetupType,
    Signal,
    SignalDirection,
    SnDZone,
    SndPattern,
    SweepEvent,
    Trend,
)
from polybot.signal.pivots import PivotTracker, find_all_pivots
from polybot.signal.snd_zones import MalaysianSndScanner, SndDetectorParams
from polybot.truth.models import BtcTick, Candle


@dataclass
class StrategyParams:
    """Subset of strategy_params.yaml the engine actually consumes."""

    pivot_lookback: int = 2
    young_trend_max_tlq_breaks: int = 3
    cold_start_lookback: int = 200
    ipa_consecutive_miss_threshold: int = 2
    sweep_min_breach_bps: int = 1
    break_min_breach_bps: int = 1
    snd_params: SndDetectorParams = field(default_factory=SndDetectorParams)
    early_warning_at_s: int = 180
    confirmation_at_s: int = 300
    htf_enabled: bool = True
    htf_period: int = 50
    htf_against_multiplier: Decimal = Decimal("0.5")
    volume_filter_enabled: bool = False
    min_signal_confidence: Decimal = Decimal("0.55")
    continuation_priors_path: str = "state/continuation_priors.json"
    fail_open_on_missing_priors: bool = False
    continuation_filter: ContinuationFilterParams = field(
        default_factory=ContinuationFilterParams
    )


def load_continuation_priors(path: Path | str) -> tuple[dict[tuple[str, str], Decimal], dict[str, object]]:
    """Load priors keyed by (setup_type, depth_bucket).

    Returns (priors_dict, metadata) where metadata has 'sample_size',
    'provisional', 'window_start_ms', 'window_end_ms' fields.
    """
    p = Path(path)
    if not p.exists():
        return {}, {"missing": True}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("continuation_priors: failed to parse {}: {}", p, exc)
        return {}, {"missing": True, "parse_error": str(exc)}
    cells = {}
    for row in data.get("cells", []):
        key = (row["setup_type"], row["depth_bucket"])
        cells[key] = Decimal(str(row["continuation_probability"]))
    meta = {k: v for k, v in data.items() if k != "cells"}
    return cells, meta


# ============================================================================
# Engine
# ============================================================================


class PriceActionEngine:
    """The orchestrator. Single-threaded; the caller drives ticks/candles."""

    def __init__(self, params: StrategyParams | None = None) -> None:
        self._params = params or StrategyParams()
        self._state = PAState()
        self._candles: list[Candle] = []
        self._pivot_tracker = PivotTracker(lookback=self._params.pivot_lookback)
        self._hierarchy: LiquidityHierarchy | None = None
        self._efficiency = EfficiencyTracker(
            ipa_consecutive_miss_threshold=self._params.ipa_consecutive_miss_threshold
        )
        self._sweep: SweepDetector | None = None
        self._htf = HtfFilter(period=self._params.htf_period)
        self._snd_scanner = MalaysianSndScanner(self._params.snd_params)
        self._recent_ticks: list[BtcTick] = []
        self._tick_buffer_size = 1024
        # The slot that the engine is currently watching for Alert-2/3 firing
        self._alert2_fired_for_slot: int | None = None
        # Consecutive TLQ breaks with no entry (for macro_cycle_reset predicate)
        self._consecutive_tlq_breaks_no_entry: int = 0
        # Continuation priors loaded from disk
        self._priors, self._priors_meta = load_continuation_priors(
            self._params.continuation_priors_path
        )

    # ------------------------------------------------------------------
    # Cold-start (§6.2.9)
    # ------------------------------------------------------------------

    def bootstrap_from_history(self, candles: Sequence[Candle]) -> None:
        """Deterministic bootstrap from raw candle history.

        Idempotent — running twice on the same data produces identical state.
        Never asks the operator a question.
        """
        if len(candles) < self._params.pivot_lookback * 2 + 1:
            log.warning(
                "bootstrap: too few candles ({}); engine in NEW_TREND_PENDING",
                len(candles),
            )
            self._state.trend = Trend.NEW_TREND_PENDING
            return

        self._candles = list(candles)

        # STEP 1: Find absolute Main High and Main Low
        rows = [(i, c.high, c.low) for i, c in enumerate(candles)]
        high_idx, low_idx = M.find_main_extremes(rows)

        # STEP 2: Determine current trend direction (corrected per Phase 3 deviation log)
        trend = M.determine_cold_start_trend(
            main_high_ts=candles[high_idx].ts_ms,
            main_low_ts=candles[low_idx].ts_ms,
        )
        self._state.trend = trend

        # STEP 3: Run pivot detection on the entire history.
        all_pivots = find_all_pivots(candles, lookback=self._params.pivot_lookback)
        self._state.confirmed_pivots = list(all_pivots)

        if trend is Trend.NEW_TREND_PENDING:
            return

        self._hierarchy = LiquidityHierarchy(trend)

        # Seed MAIN from the appropriate extreme
        if trend is Trend.DOWN:
            anchor_idx = high_idx
            anchor_price = candles[high_idx].high
            anchor_pivot = Pivot(
                index=high_idx,
                timestamp_ms=candles[high_idx].ts_ms,
                price=anchor_price,
                type=PivotType.HIGH,
                is_confirmed=True,
            )
            self._hierarchy.seed_main(anchor_pivot)
            other_pivot = Pivot(
                index=low_idx, timestamp_ms=candles[low_idx].ts_ms,
                price=candles[low_idx].low, type=PivotType.LOW, is_confirmed=True,
            )
            self._hierarchy.seed_secondary_extreme(other_pivot)
        else:
            anchor_idx = low_idx
            anchor_pivot = Pivot(
                index=low_idx, timestamp_ms=candles[low_idx].ts_ms,
                price=candles[low_idx].low, type=PivotType.LOW, is_confirmed=True,
            )
            self._hierarchy.seed_main(anchor_pivot)
            other_pivot = Pivot(
                index=high_idx, timestamp_ms=candles[high_idx].ts_ms,
                price=candles[high_idx].high, type=PivotType.HIGH, is_confirmed=True,
            )
            self._hierarchy.seed_secondary_extreme(other_pivot)

        # STEP 3 cont: feed pivots strictly AFTER the anchor candle
        post_anchor = [p for p in all_pivots if p.index > anchor_idx]
        for p in post_anchor:
            self._hierarchy.update(p)

        # STEP 6 / 8: efficiency state — we approximate by walking historical
        # TLQ-break events and counting touches between them. The full walk is
        # complex; for cold-start we initialize EfficiencyTracker to EFFICIENT
        # and let live data drive transitions. This is documented as a
        # bootstrap simplification (§6.2.9 STEP 8 pessimistic default).

        # Set up the sweep detector with current levels
        self._sweep = SweepDetector(trend, basis_bps=self._params.sweep_min_breach_bps)
        self._sweep.set_levels(
            ilq=self._hierarchy.current_ilq(),
            slq=self._hierarchy.current_slq(),
            unfilled_tlq_for_bridge=self._hierarchy.current_tlq(),
        )

        # Mirror into PAState
        self._state.main_high = self._hierarchy.current_main_high()
        self._state.main_low = self._hierarchy.current_main_low()
        self._state.slq = self._hierarchy.current_slq()
        self._state.tlq = self._hierarchy.current_tlq()
        self._state.ilq = self._hierarchy.current_ilq()
        self._state.efficiency_state = self._efficiency.state

        log.info(
            "bootstrap: trend={} main_high={} main_low={} slq={} tlq={} ilq={}",
            trend.value,
            self._state.main_high.price if self._state.main_high else None,
            self._state.main_low.price if self._state.main_low else None,
            self._state.slq.price if self._state.slq else None,
            self._state.tlq.price if self._state.tlq else None,
            self._state.ilq.price if self._state.ilq else None,
        )

        # Persist state snapshot (§6.2.9 STEP 9)
        self._persist_state()

    def _persist_state(self) -> None:
        try:
            from config.settings import PROJECT_ROOT

            path = PROJECT_ROOT / "state" / "pa_engine.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            snap = {
                "trend": self._state.trend.value,
                "main_high": str(self._state.main_high.price) if self._state.main_high else None,
                "main_low": str(self._state.main_low.price) if self._state.main_low else None,
                "slq": str(self._state.slq.price) if self._state.slq else None,
                "tlq": str(self._state.tlq.price) if self._state.tlq else None,
                "ilq": str(self._state.ilq.price) if self._state.ilq else None,
                "efficiency_state": self._state.efficiency_state.value,
                "n_confirmed_pivots": len(self._state.confirmed_pivots),
            }
            path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("pa_engine: failed to persist state: {}", exc)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def on_tick(self, tick: BtcTick) -> None:
        self._recent_ticks.append(tick)
        if len(self._recent_ticks) > self._tick_buffer_size:
            self._recent_ticks = self._recent_ticks[-self._tick_buffer_size :]

    def on_candle_close(self, candle: Candle) -> Signal | None:
        """Process a closed candle. Returns a Signal if Alert-3 fires."""
        self._candles.append(candle)

        # Snapshot TLQ break count before this candle's pivot updates
        prev_break_count = (
            self._hierarchy.confirmed_tlq_breaks_since_slq
            if self._hierarchy is not None else 0
        )

        # Update pivots
        new_confirmed = self._pivot_tracker.on_candle_close(candle)
        for p in new_confirmed:
            self._state.confirmed_pivots.append(p)
            if self._hierarchy is not None:
                self._hierarchy.update(p)

        if self._hierarchy is None or self._sweep is None:
            return None

        new_break_count = self._hierarchy.confirmed_tlq_breaks_since_slq

        # Detect sweeps from this candle's high/low
        sweep_event, new_bridge = self._sweep.on_candle(
            ts_ms=candle.ts_ms, high=candle.high, low=candle.low
        )

        # Refresh the sweep detector's level pointers (TLQ may have updated)
        self._sweep.set_levels(
            ilq=self._hierarchy.current_ilq(),
            slq=self._hierarchy.current_slq(),
            unfilled_tlq_for_bridge=self._hierarchy.current_tlq(),
        )

        # Emit Alert 1 if a sweep was validated
        if sweep_event is not None and sweep_event.validated_immediately:
            self._emit_alert(AlertType.PRE_ALERT, candle.ts_ms, sweep_event)

        # Run invalidation predicates.
        # AIL = nearest structural high above swept ILQ. Proper implementation
        # requires tracking the sweep state (ILQ only becomes AIL after a
        # confirmed ILQ sweep event). Until that logic is wired in, ail=None
        # keeps dynamic_structural_invalidation dormant without breaking other
        # predicates.
        ail = None
        ctx = InvalidationContext(
            direction=self._hierarchy.direction,
            candle_close=candle.close,
            candle_high=candle.high,
            candle_low=candle.low,
            candle_ts_ms=candle.ts_ms,
            main_high=self._hierarchy.current_main_high(),
            main_low=self._hierarchy.current_main_low(),
            slq=self._hierarchy.current_slq(),
            ail=ail,
            is_young_trend=(
                self._hierarchy.confirmed_tlq_breaks_since_slq
                < self._params.young_trend_max_tlq_breaks
            ),
            efficiency_state=self._efficiency.state,
            consecutive_misses=self._efficiency.consecutive_misses,
            consecutive_break_count_no_entry=self._consecutive_tlq_breaks_no_entry,
            basis_bps=self._params.break_min_breach_bps,
        )
        invalidation = first_invalidation(ctx)
        if invalidation is not None:
            self._state.latest_invalidation = invalidation
            log.info("invalidation: {}", invalidation.rationale)

        # Map enabled SnD zones near the validated scan zone (between ILQ and TLQ).
        # For the minimal-Phase-3 path, we scan the last K candles.
        ilq = self._hierarchy.current_ilq()
        tlq = self._hierarchy.current_tlq()
        if ilq is not None and tlq is not None and len(self._candles) >= 5:
            scan_start = max(0, len(self._candles) - 20)
            zones = self._snd_scanner.scan_range(self._candles, scan_start, len(self._candles))
            # Filter by direction matching the trend and not expired
            now = candle.ts_ms
            zones = [
                z for z in zones
                if (
                    self._hierarchy.direction is Trend.DOWN and z.direction == "SUPPLY"
                    or self._hierarchy.direction is Trend.UP and z.direction == "DEMAND"
                )
                and not z.is_expired(now)
            ]
            self._state.active_zones = zones

        # Refresh PAState
        self._state.main_high = self._hierarchy.current_main_high()
        self._state.main_low = self._hierarchy.current_main_low()
        self._state.slq = self._hierarchy.current_slq()
        self._state.tlq = self._hierarchy.current_tlq()
        self._state.ilq = self._hierarchy.current_ilq()
        self._state.efficiency_state = self._efficiency.state

        # Alert 3 candidate: closed candle wicked into an active zone and
        # closed back out of it.
        signal = self._maybe_emit_alert3(candle)
        if signal is not None:
            self._state.latest_signal = signal

        # Update consecutive-TLQ-break-no-entry counter for macro_cycle_reset predicate.
        if new_break_count > prev_break_count:
            if signal is not None:
                self._consecutive_tlq_breaks_no_entry = 0
            else:
                self._consecutive_tlq_breaks_no_entry += 1
        elif signal is not None:
            self._consecutive_tlq_breaks_no_entry = 0

        return signal

    # ------------------------------------------------------------------
    # Alert 3 / Signal emission
    # ------------------------------------------------------------------

    def _maybe_emit_alert3(self, candle: Candle) -> Signal | None:
        if not self._state.active_zones:
            return None
        if self._state.latest_invalidation is not None and (
            self._state.latest_invalidation.timestamp_ms == candle.ts_ms
        ):
            return None
        if self._hierarchy is None:
            return None
        if self._efficiency.state is EfficiencyState.IPA_FROZEN:
            return None

        direction = (
            SignalDirection.DOWN if self._hierarchy.direction is Trend.DOWN
            else SignalDirection.UP
        )

        for zone in self._state.active_zones:
            # Wick must overlap zone, close must be on signal side
            if zone.direction == "SUPPLY":
                wick_in = candle.high >= zone.bottom
                close_outside = candle.close < zone.bottom
            else:
                wick_in = candle.low <= zone.top
                close_outside = candle.close > zone.top
            if not (wick_in and close_outside):
                continue

            # Continuation filter (§1.5.1)
            ck = continuation_evaluate(
                candle=candle,
                zone=zone,
                recent_ticks=self._recent_ticks,
                signal_direction=direction,
                params=self._params.continuation_filter,
            )
            if not ck.passed:
                log.info(
                    "alert3: continuation filter rejected zone={} co={} pen_ok={} slope_ok={}",
                    zone.zone_id, ck.close_open_agreement, ck.penetration_bps_ok, ck.tick_slope_agreement_ok,
                )
                continue

            # Compose setup_type and depth_bucket
            setup_type = _setup_type_from(zone.structure_type, swept="ILQ")  # simplified
            depth_str = M.depth_bucket_from_bps(ck.penetration_bps)
            depth_bucket = DepthBucket(depth_str)

            prior = self._priors.get((setup_type.value, depth_bucket.value))
            if prior is None:
                # Fall back to setup-type marginal if available
                marginal_keys = [k for k in self._priors if k[0] == setup_type.value]
                if marginal_keys:
                    prior = sum((self._priors[k] for k in marginal_keys), Decimal(0)) / Decimal(len(marginal_keys))
                else:
                    prior = Decimal("0.50") if self._params.fail_open_on_missing_priors else None
            if prior is None:
                log.info(
                    "alert3: no continuation prior for ({}, {}); refusing entry",
                    setup_type.value, depth_bucket.value,
                )
                continue

            freshness = zone.freshness_at(candle.ts_ms)
            htf_mult = (
                self._htf.alignment_multiplier(
                    self._candles, direction.value,
                    against_multiplier=self._params.htf_against_multiplier,
                )
                if self._params.htf_enabled
                else Decimal("1.0")
            )
            volume_mult = Decimal("1.0")  # disabled by default per §1.5.4
            confidence = (
                prior * freshness * htf_mult * volume_mult * zone.pattern_confidence
            )

            invalidation_level = (
                self._hierarchy.current_slq().price
                if self._hierarchy.current_slq() is not None
                else (self._hierarchy.current_main_high().price if self._hierarchy.current_main_high() else candle.close)
            )

            slot_end_ms = candle.ts_ms + 300_000
            confirmation_age_ms = candle.ts_ms - zone.formed_at_ms

            sig = Signal(
                direction=direction,
                setup_type=setup_type,
                depth_bucket=depth_bucket,
                confidence=min(Decimal(1), max(Decimal(0), confidence)),
                continuation_prior=prior,
                snd_zone_id=zone.zone_id,
                invalidation_level=invalidation_level,
                expires_at_slot_end_ms=slot_end_ms + 300_000,  # signal valid for next slot
                confirmation_age_ms=confirmation_age_ms,
                rejection_depth_bps=ck.penetration_bps,
                timestamp_ms=candle.ts_ms,
                rationale=(
                    f"Alert3: {setup_type.value} {direction.value} on zone "
                    f"{zone.zone_id} (pen {ck.penetration_bps:.1f}bps, "
                    f"slope {ck.tick_slope_agreement_fraction:.2f}, "
                    f"freshness {freshness:.2f}, htf {htf_mult})"
                ),
                freshness_factor=freshness,
                htf_alignment_factor=htf_mult,
                volume_filter_factor=volume_mult,
                pattern_confidence=zone.pattern_confidence,
            )

            # Confidence floor
            if sig.confidence < self._params.min_signal_confidence:
                log.info(
                    "alert3: confidence {} < min {}; skipping",
                    sig.confidence, self._params.min_signal_confidence,
                )
                continue

            self._emit_alert(AlertType.EXECUTION, candle.ts_ms, sig)
            return sig
        return None

    def _emit_alert(self, alert_type: AlertType, ts_ms: int, payload: object) -> None:
        self._state.last_alerts_emitted[alert_type] = ts_ms
        log.info(
            "alert: type={} ts={} payload_id={}",
            alert_type.value, ts_ms,
            getattr(payload, "node_id", getattr(payload, "snd_zone_id", "")),
        )

    # ------------------------------------------------------------------
    # Read accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> PAState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._hierarchy is not None and self._state.trend is not Trend.NEW_TREND_PENDING


def _setup_type_from(pattern: SndPattern, *, swept: str) -> SetupType:
    """Map (pattern, swept-node) to a SetupType key for the priors table."""
    suffix = "ILQ" if swept == "ILQ" else "SLQ"
    if pattern is SndPattern.DBD:
        return SetupType.DBD_ILQ if suffix == "ILQ" else SetupType.DBD_SLQ
    if pattern is SndPattern.RBD:
        return SetupType.RBD_ILQ if suffix == "ILQ" else SetupType.RBD_SLQ
    if pattern is SndPattern.DOJI:
        return SetupType.DOJI_ILQ
    if pattern is SndPattern.INSIDE_BAR:
        return SetupType.INSIDE_ILQ
    if pattern is SndPattern.SND_GAP:
        return SetupType.SND_GAP_ILQ
    return SetupType.DBD_ILQ
