"""Verify the locked-in 2026 facts haven't drifted accidentally."""

from __future__ import annotations

from decimal import Decimal

from config import constants as K


def test_v2_invariants() -> None:
    K.assert_constants_sane()
    assert K.EIP712_EXCHANGE_DOMAIN_VERSION == "2"
    assert K.EIP712_CLOBAUTH_DOMAIN_VERSION == "1"
    assert K.POLYGON_CHAIN_ID == 137


def test_cutover_timestamp_matches_2026_04_28_11_utc() -> None:
    """Lock the cutover ms constant to the wall-clock fact."""
    import datetime as dt

    expected = int(
        dt.datetime(2026, 4, 28, 11, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000
    )
    assert K.CLOB_V2_CUTOVER_MS == expected, (
        f"CLOB_V2_CUTOVER_MS drift: have {K.CLOB_V2_CUTOVER_MS}, "
        f"want {expected} (2026-04-28 11:00:00 UTC)"
    )


def test_v2_order_struct_excludes_v1_fields() -> None:
    removed = set(K.V2_ORDER_REMOVED_FIELDS)
    fields = set(K.V2_ORDER_FIELDS)
    assert removed.isdisjoint(fields), (
        f"V2 order fields must NOT include any V1-only fields: {removed & fields}"
    )
    # The exact V2 shape from §2.1
    assert "salt" in fields
    assert "signature" in fields
    assert "builder" in fields
    assert "nonce" not in fields  # the canonical drop


def test_fee_schedule() -> None:
    # Current crypto schedule: peak 1.56% at p=0.50
    assert K.FEE_RATE_CRYPTO_CURRENT == Decimal("0.25")
    assert K.FEE_EXPONENT_CRYPTO_CURRENT == 2
    assert K.PEAK_EFFECTIVE_FEE_CRYPTO_CURRENT == Decimal("0.0156")
    # Post-March-30 schedule: peak 1.80% at p=0.50
    assert K.FEE_RATE_CRYPTO_POST_MARCH_30 == Decimal("0.072")
    assert K.FEE_EXPONENT_CRYPTO_POST_MARCH_30 == 1
    assert K.PEAK_EFFECTIVE_FEE_CRYPTO_POST_MARCH_30 == Decimal("0.0180")


def test_slot_arithmetic() -> None:
    assert K.SLOT_DURATION_S == 300
    assert K.SLOT_DURATION_MS == 300 * 1000
    assert K.BTC_UPDOWN_5M_SLUG_TEMPLATE.format(slot_end_ts=1700000000) == (
        "btc-updown-5m-1700000000"
    )


def test_min_order_and_batch() -> None:
    assert K.MIN_ORDER_SHARES == 5
    assert K.BATCH_ORDER_LIMIT == 15
    assert K.POST_ONLY_SUPPORTED is True


def test_verifying_contracts_are_valid_addresses() -> None:
    for addr in (K.CTF_EXCHANGE_V2_ADDRESS, K.NEG_RISK_CTF_EXCHANGE_V2_ADDRESS):
        assert addr.startswith("0x")
        assert len(addr) == 42  # 0x + 40 hex chars
