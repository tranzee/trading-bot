"""Smoke tests for config.settings — environment loading and validation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config.settings import RunMode, Settings


@pytest.fixture
def base_env() -> dict[str, str]:
    return {
        "POLYMARKET_PRIVATE_KEY": "0x" + "11" * 32,
        "POLYMARKET_FUNDER_ADDRESS": "0x" + "ab" * 20,
    }


def _settings(env: dict[str, str]) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_loads_with_defaults(base_env: dict[str, str]) -> None:
    s = _settings(base_env)
    assert s.RUN_MODE is RunMode.PAPER
    assert s.is_paper
    assert not s.is_live
    assert s.MIN_SIGNAL_CONFIDENCE == Decimal("0.55")
    assert s.btc_sources == ("binance", "chainlink", "coinbase")
    # Default host targets the pre-cutover testnet; flips at operator's discretion.
    assert s.POLYMARKET_HOST == "https://clob-v2.polymarket.com"


def test_rejects_bad_funder_address(base_env: dict[str, str]) -> None:
    bad = dict(base_env)
    bad["POLYMARKET_FUNDER_ADDRESS"] = "not-an-address"
    with pytest.raises(ValueError):
        _settings(bad)


def test_rejects_unknown_btc_source(base_env: dict[str, str]) -> None:
    bad = dict(base_env)
    bad["BTC_SOURCES"] = "binance,kraken"
    with pytest.raises(ValueError):
        _settings(bad)


def test_rejects_confidence_above_one(base_env: dict[str, str]) -> None:
    bad = dict(base_env)
    bad["MIN_SIGNAL_CONFIDENCE"] = "1.5"
    with pytest.raises(ValueError):
        _settings(bad)


def test_private_key_is_secret(base_env: dict[str, str]) -> None:
    s = _settings(base_env)
    # SecretStr never reveals the value when stringified
    assert "**********" in repr(s.POLYMARKET_PRIVATE_KEY)
    assert base_env["POLYMARKET_PRIVATE_KEY"] not in repr(s)
