"""Malaysian SnD zone detection (§6.2.5).

Tier-A patterns (mechanical, enabled by default):
    1. Inside Bar  — high[i] < high[i-1] AND low[i] > low[i-1]
    2. Doji        — abs(close - open) / (high - low) < doji_body_ratio
    3. DBD         — drop / base / drop
    4. RBD         — rally / base / drop
    5. SnD Gap     — wick gap between candle bodies

Tier-B (subjective; stubbed and OFF by default per §1.5.3):
    Apex, A-Shape, Left Shoulder, SBR
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from polybot.signal.models import SnDZone, SndPattern
from polybot.truth.models import Candle


@dataclass(frozen=True)
class SndDetectorParams:
    base_min_candles: int = 1
    base_max_candles: int = 6
    doji_body_ratio: Decimal = Decimal("0.10")
    dbd_min_leg_bps: int = 5
    rbd_min_leg_bps: int = 5
    inside_bar_max_overlap_pct: int = 100  # not yet used; reserved
    inside_bar_min_range_bps: int = 5      # noise guard: inside bar range must be >= N bps
    dbd_base_band_multiplier: Decimal = Decimal("0.5")   # base candles ± N × |leg body|
    rbd_base_band_multiplier: Decimal = Decimal("0.5")
    half_life_min: Decimal = Decimal("30")
    max_age_min: Decimal = Decimal("120")
    enabled_patterns: tuple[SndPattern, ...] = field(
        default_factory=lambda: (
            SndPattern.INSIDE_BAR,
            SndPattern.DOJI,
            SndPattern.DBD,
            SndPattern.RBD,
            SndPattern.SND_GAP,
        )
    )


def _bps(a: Decimal, b: Decimal) -> Decimal:
    if b == 0:
        return Decimal(0)
    return ((a - b) / b) * Decimal(10_000)


def detect_inside_bar(
    candles: Sequence[Candle],
    i: int,
    *,
    params: SndDetectorParams | None = None,
) -> SnDZone | None:
    """Inside bar at index i: i's high < i-1's high AND i's low > i-1's low.

    Direction is inferred from the body direction of the inside bar: red
    body -> SUPPLY zone (downtrend setup), green -> DEMAND. The zone bounds
    span the inside-bar's full range (high to low).

    Range guard: the inside bar's range must meet the minimum bps threshold
    to filter out noise during ultra-low-volatility consolidation.
    """
    if i < 1:
        return None
    prev = candles[i - 1]
    cur = candles[i]
    if not (cur.high < prev.high and cur.low > prev.low):
        return None
    # Range guard: reject microscopic inside bars (noise)
    min_range_bps = params.inside_bar_min_range_bps if params is not None else 5
    rng = cur.high - cur.low
    if cur.low > 0 and rng / cur.low * Decimal("10000") < Decimal(str(min_range_bps)):
        return None
    direction = "SUPPLY" if cur.close < cur.open else "DEMAND"
    confidence = Decimal("0.6")
    return SnDZone(
        top=cur.high,
        bottom=cur.low,
        structure_type=SndPattern.INSIDE_BAR,
        direction=direction,
        formed_at_ms=cur.ts_ms,
        source_candle_indices=(i - 1, i),
        formation_volume_ratio=Decimal(1),
        pattern_confidence=confidence,
        half_life_min=Decimal("30"),
        max_age_min=Decimal("120"),
    )


def detect_doji(
    candles: Sequence[Candle], i: int, *, body_ratio_max: Decimal
) -> SnDZone | None:
    cur = candles[i]
    rng = cur.high - cur.low
    if rng <= 0:
        return None
    body = abs(cur.close - cur.open)
    if body / rng >= body_ratio_max:
        return None
    # Doji direction inferred from the next candle's direction if available;
    # otherwise default SUPPLY (the engine will filter via trend context).
    direction = "SUPPLY"
    if i + 1 < len(candles):
        nxt = candles[i + 1]
        direction = "SUPPLY" if nxt.close < nxt.open else "DEMAND"
    # Pattern confidence scales with how thin the body is
    confidence = max(Decimal("0.3"), Decimal(1) - (body / rng) / body_ratio_max)
    return SnDZone(
        top=cur.high,
        bottom=cur.low,
        structure_type=SndPattern.DOJI,
        direction=direction,
        formed_at_ms=cur.ts_ms,
        source_candle_indices=(i,),
        formation_volume_ratio=Decimal(1),
        pattern_confidence=min(Decimal(1), confidence),
        half_life_min=Decimal("30"),
        max_age_min=Decimal("120"),
    )


def detect_dbd(
    candles: Sequence[Candle], i: int, *, params: SndDetectorParams
) -> SnDZone | None:
    """Drop-Base-Drop: leg-1 drop, base of 1..base_max candles, leg-2 drop.

    Returns a zone at the BASE's high to base's low (supply zone).
    """
    base_max = params.base_max_candles
    base_min = params.base_min_candles
    # Try every base length from base_min..base_max. Pattern shape:
    #   candles[i - base_len - 1] = leg-1 (drop)
    #   candles[i - base_len : i]  = base (size = base_len)
    #   candles[i]                  = leg-2 (drop, the trigger candle)
    # The trigger candle is at the rightmost end; we don't require any candle after it.
    for base_len in range(base_min, base_max + 1):
        if i - base_len - 1 < 0:
            continue
        leg1 = candles[i - base_len - 1]
        base_slice = candles[i - base_len : i]
        leg2 = candles[i]

        # leg-1 must be a real drop
        leg1_drop_bps = _bps(leg1.open, leg1.close)
        if leg1_drop_bps < params.dbd_min_leg_bps:
            continue
        # leg-2 must be a real drop
        leg2_drop_bps = _bps(leg2.open, leg2.close)
        if leg2_drop_bps < params.dbd_min_leg_bps:
            continue
        # Base body-overlap heuristic: base candles must stay near leg-1 close.
        # Band = N × |leg-1 body| — previously 0.5, now configurable (default 1.0).
        anchor = leg1.close
        band = abs(leg1.open - leg1.close) * params.dbd_base_band_multiplier
        ok = all(c.high <= anchor + band and c.low >= anchor - band for c in base_slice)
        if not ok:
            continue

        zone_top = max(c.high for c in base_slice)
        zone_bot = min(c.low for c in base_slice)
        if zone_top <= zone_bot:
            continue
        return SnDZone(
            top=zone_top,
            bottom=zone_bot,
            structure_type=SndPattern.DBD,
            direction="SUPPLY",
            formed_at_ms=base_slice[-1].ts_ms,
            source_candle_indices=tuple(range(i - base_len - 1, i + 1)),
            formation_volume_ratio=Decimal(1),
            pattern_confidence=Decimal("0.75"),
            half_life_min=params.half_life_min,
            max_age_min=params.max_age_min,
        )
    return None


def detect_rbd(
    candles: Sequence[Candle], i: int, *, params: SndDetectorParams
) -> SnDZone | None:
    """Rally-Base-Drop: leg-1 rally, base, leg-2 drop. Supply zone."""
    base_max = params.base_max_candles
    base_min = params.base_min_candles
    for base_len in range(base_min, base_max + 1):
        if i - base_len - 1 < 0:
            continue
        leg1 = candles[i - base_len - 1]
        base_slice = candles[i - base_len : i]
        leg2 = candles[i]

        leg1_rally_bps = _bps(leg1.close, leg1.open)
        if leg1_rally_bps < params.rbd_min_leg_bps:
            continue
        leg2_drop_bps = _bps(leg2.open, leg2.close)
        if leg2_drop_bps < params.rbd_min_leg_bps:
            continue

        anchor = leg1.close
        band = abs(leg1.close - leg1.open) * params.rbd_base_band_multiplier
        ok = all(c.high <= anchor + band and c.low >= anchor - band for c in base_slice)
        if not ok:
            continue

        zone_top = max(c.high for c in base_slice)
        zone_bot = min(c.low for c in base_slice)
        if zone_top <= zone_bot:
            continue
        return SnDZone(
            top=zone_top,
            bottom=zone_bot,
            structure_type=SndPattern.RBD,
            direction="SUPPLY",
            formed_at_ms=base_slice[-1].ts_ms,
            source_candle_indices=tuple(range(i - base_len - 1, i + 1)),
            formation_volume_ratio=Decimal(1),
            pattern_confidence=Decimal("0.7"),
            half_life_min=params.half_life_min,
            max_age_min=params.max_age_min,
        )
    return None


def detect_snd_gap(candles: Sequence[Candle], i: int) -> SnDZone | None:
    """SnD Gap: candle i+1's body does not overlap candle i's body.

    The zone is the gap between bodies — i's body high to i+1's body low
    (SUPPLY) or symmetric (DEMAND).
    """
    if i + 1 >= len(candles):
        return None
    a = candles[i]
    b = candles[i + 1]
    a_body_top = max(a.open, a.close)
    a_body_bot = min(a.open, a.close)
    b_body_top = max(b.open, b.close)
    b_body_bot = min(b.open, b.close)

    # Gap DOWN: a's body bottom > b's body top
    if a_body_bot > b_body_top:
        return SnDZone(
            top=a_body_bot,
            bottom=b_body_top,
            structure_type=SndPattern.SND_GAP,
            direction="SUPPLY",
            formed_at_ms=b.ts_ms,
            source_candle_indices=(i, i + 1),
            formation_volume_ratio=Decimal(1),
            pattern_confidence=Decimal("0.6"),
            half_life_min=Decimal("30"),
            max_age_min=Decimal("120"),
        )
    # Gap UP: b's body bottom > a's body top
    if b_body_bot > a_body_top:
        return SnDZone(
            top=b_body_bot,
            bottom=a_body_top,
            structure_type=SndPattern.SND_GAP,
            direction="DEMAND",
            formed_at_ms=b.ts_ms,
            source_candle_indices=(i, i + 1),
            formation_volume_ratio=Decimal(1),
            pattern_confidence=Decimal("0.6"),
            half_life_min=Decimal("30"),
            max_age_min=Decimal("120"),
        )
    return None


class MalaysianSndScanner:
    """Run all enabled Tier-A detectors over a candle range."""

    def __init__(self, params: SndDetectorParams | None = None) -> None:
        self._params = params or SndDetectorParams()

    def scan_range(
        self,
        candles: Sequence[Candle],
        start_idx: int,
        end_idx: int,
    ) -> list[SnDZone]:
        zones: list[SnDZone] = []
        end_idx = min(end_idx, len(candles))
        for i in range(max(0, start_idx), end_idx):
            for pat in self._params.enabled_patterns:
                zone = self._detect_one(candles, i, pat)
                if zone is not None:
                    zones.append(zone)
        return _dedupe(zones)

    def _detect_one(
        self, candles: Sequence[Candle], i: int, pattern: SndPattern
    ) -> SnDZone | None:
        if pattern is SndPattern.INSIDE_BAR:
            return detect_inside_bar(candles, i, params=self._params)
        if pattern is SndPattern.DOJI:
            return detect_doji(candles, i, body_ratio_max=self._params.doji_body_ratio)
        if pattern is SndPattern.DBD:
            return detect_dbd(candles, i, params=self._params)
        if pattern is SndPattern.RBD:
            return detect_rbd(candles, i, params=self._params)
        if pattern is SndPattern.SND_GAP:
            return detect_snd_gap(candles, i)
        return None  # Tier-B not implemented


def _dedupe(zones: list[SnDZone]) -> list[SnDZone]:
    """Deduplicate overlapping zones; keep highest pattern_confidence per overlap."""
    if not zones:
        return zones
    zones_sorted = sorted(zones, key=lambda z: (-z.pattern_confidence, z.formed_at_ms))
    out: list[SnDZone] = []
    for z in zones_sorted:
        overlap = any(
            z.direction == ex.direction
            and not (z.top < ex.bottom or z.bottom > ex.top)
            for ex in out
        )
        if not overlap:
            out.append(z)
    return out
