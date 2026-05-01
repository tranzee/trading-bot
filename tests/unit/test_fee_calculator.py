"""Fee math — protocol formula sanity checks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config import constants as K
from polybot.poly.fee_calculator import (
    crypto_current_schedule,
    crypto_post_march_30_schedule,
    expected_maker_rebate,
    expected_taker_fee,
)


def test_peak_fee_is_at_p_50_under_current_schedule() -> None:
    """Current schedule (rate=0.25, exp=2) peaks at p=0.50 with effective ~1.56%."""
    fee_rate, exponent = crypto_current_schedule()
    # At p=0.50, fee per share = 0.5 * 0.25 * (0.5*0.5)^2 = 0.5 * 0.25 * 0.0625 = 0.0078125
    # That's 0.0078125 / 0.5 (per dollar bet) = 1.5625%
    fee = expected_taker_fee(shares=100, price=Decimal("0.50"), fee_rate=fee_rate, exponent=exponent)
    # Total notional = 100 * 0.50 = $50; fee at peak = $50 * 1.5625% = $0.78125
    # Decimal.quantize() defaults to ROUND_HALF_EVEN (banker's rounding), so
    # 0.78125 -> 0.7812 (the nearest *even* last digit).
    assert fee == Decimal("0.7812")


def test_fee_drops_off_in_tails() -> None:
    fee_rate, exponent = crypto_current_schedule()
    fee_p50 = expected_taker_fee(shares=100, price=Decimal("0.50"), fee_rate=fee_rate, exponent=exponent)
    fee_p10 = expected_taker_fee(shares=100, price=Decimal("0.10"), fee_rate=fee_rate, exponent=exponent)
    fee_p90 = expected_taker_fee(shares=100, price=Decimal("0.90"), fee_rate=fee_rate, exponent=exponent)
    assert fee_p10 < fee_p50
    assert fee_p90 < fee_p50
    # Tails should be small: at p=0.10 the (p*(1-p))^2 term is tiny
    assert fee_p10 < Decimal("0.05")


def test_post_march_30_schedule_peaks_at_180bps() -> None:
    fee_rate, exponent = crypto_post_march_30_schedule()
    # exp=1, peak fee/share = 0.5 * 0.072 * 0.25 = 0.009 -> 0.009/0.5 = 1.80%
    fee = expected_taker_fee(shares=100, price=Decimal("0.50"), fee_rate=fee_rate, exponent=exponent)
    # 100 * 0.50 * 1.80% = $0.9000
    assert fee == Decimal("0.9000")


def test_maker_rebate_is_20pct_of_fee() -> None:
    fee_rate, exponent = crypto_current_schedule()
    fee = expected_taker_fee(shares=100, price=Decimal("0.50"), fee_rate=fee_rate, exponent=exponent)
    rebate = expected_maker_rebate(shares=100, price=Decimal("0.50"), fee_rate=fee_rate, exponent=exponent)
    assert rebate == (fee * K.MAKER_REBATE_FRACTION).quantize(K.FEE_QUANTUM)


def test_rejects_invalid_inputs() -> None:
    fee_rate, exponent = crypto_current_schedule()
    with pytest.raises(ValueError):
        expected_taker_fee(shares=-1, price=Decimal("0.5"), fee_rate=fee_rate, exponent=exponent)
    with pytest.raises(ValueError):
        expected_taker_fee(shares=10, price=Decimal("1.0"), fee_rate=fee_rate, exponent=exponent)
    with pytest.raises(ValueError):
        expected_taker_fee(shares=10, price=Decimal("0"), fee_rate=fee_rate, exponent=exponent)
