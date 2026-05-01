"""Tests for the startup banner (host vs. cutover safety check)."""

from __future__ import annotations

from config import constants as K
from polybot.startup import (
    CUTOVER_GRACE_MS,
    PRECUTOVER_HOST,
    PRODUCTION_HOST,
    evaluate_host,
)


# Reference points around the cutover (2026-04-28 11:00 UTC)
JUST_BEFORE = K.CLOB_V2_CUTOVER_MS - 1
AT_CUTOVER = K.CLOB_V2_CUTOVER_MS
DURING_GRACE = K.CLOB_V2_CUTOVER_MS + (CUTOVER_GRACE_MS - 1)
AFTER_GRACE = K.CLOB_V2_CUTOVER_MS + CUTOVER_GRACE_MS + 1
DAY_BEFORE = K.CLOB_V2_CUTOVER_MS - 24 * 3_600_000
DAY_AFTER = K.CLOB_V2_CUTOVER_MS + 24 * 3_600_000


def test_precutover_with_testnet_host_is_clean() -> None:
    b = evaluate_host(PRECUTOVER_HOST, now_ms=DAY_BEFORE)
    assert b.is_precutover_window
    assert not b.is_postcutover_window
    assert b.warning is None
    assert b.is_warning is False


def test_precutover_with_production_host_warns() -> None:
    b = evaluate_host(PRODUCTION_HOST, now_ms=DAY_BEFORE)
    assert b.is_warning
    assert "BEFORE cutover" in (b.warning or "")
    assert "clob-v2.polymarket.com" in (b.warning or "")


def test_postcutover_with_production_host_is_clean() -> None:
    b = evaluate_host(PRODUCTION_HOST, now_ms=DAY_AFTER)
    assert b.is_postcutover_window
    assert b.warning is None


def test_postcutover_with_testnet_host_warns() -> None:
    b = evaluate_host(PRECUTOVER_HOST, now_ms=DAY_AFTER)
    assert b.is_warning
    assert "AFTER cutover" in (b.warning or "")


def test_grace_window_accepts_either_host() -> None:
    for host in (PRECUTOVER_HOST, PRODUCTION_HOST):
        b = evaluate_host(host, now_ms=DURING_GRACE)
        assert b.warning is None, f"{host} during grace should not warn: {b.warning}"


def test_at_cutover_boundary() -> None:
    # Exactly at cutover: in grace; either host accepted.
    b = evaluate_host(PRODUCTION_HOST, now_ms=AT_CUTOVER)
    assert b.warning is None
    b2 = evaluate_host(PRECUTOVER_HOST, now_ms=AT_CUTOVER)
    assert b2.warning is None


def test_just_before_cutover_with_production_still_warns() -> None:
    b = evaluate_host(PRODUCTION_HOST, now_ms=JUST_BEFORE)
    assert b.is_warning


def test_after_grace_with_testnet_warns() -> None:
    b = evaluate_host(PRECUTOVER_HOST, now_ms=AFTER_GRACE)
    assert b.is_warning


def test_unknown_host_warns() -> None:
    b = evaluate_host("https://example.com", now_ms=DAY_BEFORE)
    assert b.is_warning
    assert "neither" in (b.warning or "")


def test_banner_text_includes_host_and_phase() -> None:
    b = evaluate_host(PRECUTOVER_HOST, now_ms=DAY_BEFORE)
    assert PRECUTOVER_HOST in b.text
    assert "PRE-CUTOVER" in b.text
