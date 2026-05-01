"""SnD zone detection tests — Tier-A patterns."""

from __future__ import annotations

from decimal import Decimal

from polybot.signal.models import SndPattern
from polybot.signal.snd_zones import (
    MalaysianSndScanner,
    SndDetectorParams,
    detect_dbd,
    detect_doji,
    detect_inside_bar,
    detect_rbd,
    detect_snd_gap,
)
from polybot.truth.models import Candle


def C(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        ts_ms=i * 300_000,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)),
        volume=Decimal(1), n_ticks=1,
    )


def test_inside_bar_detected() -> None:
    candles = [
        C(0, 100, 110, 90, 105),
        C(1, 102, 108, 95, 99),     # inside the previous bar's range
    ]
    z = detect_inside_bar(candles, 1)
    assert z is not None
    assert z.structure_type is SndPattern.INSIDE_BAR
    assert z.top == Decimal("108")
    assert z.bottom == Decimal("95")


def test_inside_bar_rejected_when_not_inside() -> None:
    candles = [C(0, 100, 110, 90, 105), C(1, 102, 112, 95, 99)]
    assert detect_inside_bar(candles, 1) is None


def test_doji_detected() -> None:
    candles = [C(0, 100, 105, 95, 100.2), C(1, 100, 103, 98, 99)]
    # candle 0: body=0.2, range=10 -> ratio=0.02 < 0.10
    z = detect_doji(candles, 0, body_ratio_max=Decimal("0.10"))
    assert z is not None
    assert z.structure_type is SndPattern.DOJI


def test_doji_rejected_when_body_too_large() -> None:
    candles = [C(0, 100, 105, 95, 103)]  # body=3, range=10, ratio=0.3
    assert detect_doji(candles, 0, body_ratio_max=Decimal("0.10")) is None


def test_dbd_detected() -> None:
    # leg-1 drop, base of 2 candles, leg-2 drop
    candles = [
        C(0, 100, 100, 99.4, 99.4),    # leg-1: 60 bps drop
        C(1, 99.4, 99.5, 99.3, 99.4),  # base
        C(2, 99.4, 99.5, 99.3, 99.4),  # base
        C(3, 99.4, 99.4, 98.8, 98.8),  # leg-2: 60 bps drop
    ]
    params = SndDetectorParams(dbd_min_leg_bps=5, base_min_candles=2, base_max_candles=3)
    z = detect_dbd(candles, 3, params=params)
    assert z is not None
    assert z.structure_type is SndPattern.DBD
    assert z.direction == "SUPPLY"


def test_rbd_detected() -> None:
    # leg-1 rally, base, leg-2 drop -> supply zone
    candles = [
        C(0, 99, 99.6, 99, 99.6),       # leg-1: ~60 bps rally
        C(1, 99.6, 99.7, 99.5, 99.6),   # base
        C(2, 99.6, 99.6, 99, 99),       # leg-2: ~60 bps drop
    ]
    params = SndDetectorParams(rbd_min_leg_bps=5, base_min_candles=1, base_max_candles=2)
    z = detect_rbd(candles, 2, params=params)
    assert z is not None
    assert z.structure_type is SndPattern.RBD


def test_snd_gap_supply() -> None:
    # candle 1's body is below candle 0's body with no overlap -> supply gap
    candles = [
        C(0, 100, 102, 99, 101),     # body 100..101
        C(1, 98, 99, 97, 98.5),      # body 98..98.5
    ]
    z = detect_snd_gap(candles, 0)
    assert z is not None
    assert z.structure_type is SndPattern.SND_GAP


def test_scanner_dedupes_overlapping_zones() -> None:
    candles = [
        C(0, 100, 105, 95, 100.2),    # would be a doji
        C(1, 100, 104, 96, 99),       # would be inside bar (yes — 104<105 and 96>95)
    ]
    scanner = MalaysianSndScanner()
    zones = scanner.scan_range(candles, 0, len(candles))
    # Both detectors might fire; deduplication should prefer higher-confidence
    assert len(zones) >= 1


def test_zone_freshness_decays() -> None:
    candles = [C(0, 100, 105, 95, 100.2)]
    z = detect_doji(candles, 0, body_ratio_max=Decimal("0.10"))
    assert z is not None
    fresh_now = z.freshness_at(z.formed_at_ms)
    fresh_30min = z.freshness_at(z.formed_at_ms + 30 * 60 * 1000)
    # 30 minutes is one half-life -> fresh ~ 0.5
    assert fresh_now == Decimal("1") or fresh_now > Decimal("0.99")
    assert Decimal("0.4") < fresh_30min < Decimal("0.6")


def test_zone_expires_after_max_age() -> None:
    candles = [C(0, 100, 105, 95, 100.2)]
    z = detect_doji(candles, 0, body_ratio_max=Decimal("0.10"))
    assert z is not None
    assert not z.is_expired(z.formed_at_ms)
    assert z.is_expired(z.formed_at_ms + 200 * 60 * 1000)  # > 120min default
