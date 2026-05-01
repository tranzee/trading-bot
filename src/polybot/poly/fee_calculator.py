"""Local fee preview — pure math mirror of the protocol's fee formula.

NEVER used to set fees on real orders (V2 fees are protocol-set at match time).
Used by the simulator and by the strategy's exit decision: "is a taker exit at
price P worth it given current win probability and remaining time?"

Formula (MASTER_BLUEPRINT.md §2.3):
    fee = shares * price * fee_rate * (price * (1 - price)) ** exponent
"""

from __future__ import annotations

from decimal import Decimal

from config import constants as K
from polybot.poly.order_dsl import FeeDetails


def expected_taker_fee(
    *,
    shares: int,
    price: Decimal,
    fee_rate: Decimal,
    exponent: int,
) -> Decimal:
    """Pure protocol-formula preview. Returns USDC fee amount, quantized to FEE_QUANTUM."""
    if shares < 0:
        raise ValueError("shares must be non-negative")
    if not (Decimal("0") < price < Decimal("1")):
        raise ValueError(f"price must be in (0, 1); got {price}")
    p = price
    raw = (
        Decimal(shares)
        * p
        * fee_rate
        * ((p * (Decimal(1) - p)) ** exponent)
    )
    return raw.quantize(K.FEE_QUANTUM)


def expected_maker_rebate(
    *,
    shares: int,
    price: Decimal,
    fee_rate: Decimal,
    exponent: int,
    rebate_fraction: Decimal = K.MAKER_REBATE_FRACTION,
) -> Decimal:
    """Best-case rebate for a maker fill. Actual rebate depends on the daily
    pool and per-market rebate scoring; this is an upper bound for previews."""
    fee = expected_taker_fee(shares=shares, price=price, fee_rate=fee_rate, exponent=exponent)
    return (fee * rebate_fraction).quantize(K.FEE_QUANTUM)


def from_market_info(details: FeeDetails) -> tuple[Decimal, int]:
    """Helper: extract fee_rate and exponent from a typed FeeDetails."""
    return details.fee_rate, details.exponent


def crypto_current_schedule() -> tuple[Decimal, int]:
    """Documented current schedule. Use only when the market info is unreachable."""
    return K.FEE_RATE_CRYPTO_CURRENT, K.FEE_EXPONENT_CRYPTO_CURRENT


def crypto_post_march_30_schedule() -> tuple[Decimal, int]:
    """Documented post-2026-03-30 schedule for new crypto markets."""
    return K.FEE_RATE_CRYPTO_POST_MARCH_30, K.FEE_EXPONENT_CRYPTO_POST_MARCH_30
