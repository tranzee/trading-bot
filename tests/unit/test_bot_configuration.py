"""Unit tests for BotConfiguration loader (multi-config refactor)."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from config.bot_configuration import (
    BotConfiguration,
    load_all_configurations,
    load_configuration,
)


def _write_yaml(p: Path, data: dict) -> Path:
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    return _write_yaml(tmp_path / "configurations.yaml", {
        "configurations": {
            "btc_5m": {
                "asset": "BTC",
                "window_seconds": 300,
                "polymarket_slug_pattern": "btc-updown-5m-{slot_end_ts}",
                "binance_stream_pattern": "/stream?streams=btcusdt@trade/btcusdt@kline_5m",
                "coinbase_product": "BTC-USD",
                "binance_vision_pattern": "BTCUSDT-5m",
            },
            "btc_15m": {
                "asset": "BTC",
                "window_seconds": 900,
                "polymarket_slug_pattern": "btc-updown-15m-{slot_end_ts}",
                "binance_stream_pattern": "/stream?streams=btcusdt@trade/btcusdt@kline_15m",
                "coinbase_product": "BTC-USD",
                "binance_vision_pattern": "BTCUSDT-15m",
                "freshness_half_life_min": 90,
            },
        }
    })


class TestLoader:
    def test_load_all(self, yaml_path):
        cfgs = load_all_configurations(yaml_path)
        assert set(cfgs.keys()) == {"btc_5m", "btc_15m"}
        assert isinstance(cfgs["btc_5m"], BotConfiguration)

    def test_load_one(self, yaml_path):
        cfg = load_configuration("btc_5m", path=yaml_path)
        assert cfg.name == "btc_5m"
        assert cfg.asset == "BTC"
        assert cfg.window_seconds == 300

    def test_unknown_raises(self, yaml_path):
        with pytest.raises(KeyError, match="unknown configuration"):
            load_configuration("doesnotexist", path=yaml_path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_all_configurations(tmp_path / "nope.yaml")


class TestDerivedDefaults:
    def test_5m_defaults(self, yaml_path):
        cfg = load_configuration("btc_5m", path=yaml_path)
        # 12 * 300 = 3600
        assert cfg.htf_window_seconds == 3600
        # 6 * (300/60) = 30
        assert cfg.freshness_half_life_min == Decimal("30")

    def test_15m_explicit_half_life(self, yaml_path):
        cfg = load_configuration("btc_15m", path=yaml_path)
        assert cfg.window_seconds == 900
        assert cfg.htf_window_seconds == 12 * 900   # 10800
        # explicitly overridden in YAML
        assert cfg.freshness_half_life_min == Decimal("90")


class TestDerivedProperties:
    def test_window_ms(self, yaml_path):
        cfg = load_configuration("btc_5m", path=yaml_path)
        assert cfg.window_ms == 300_000

    def test_priors_path_per_config(self, yaml_path):
        c5 = load_configuration("btc_5m", path=yaml_path)
        c15 = load_configuration("btc_15m", path=yaml_path)
        assert c5.priors_path == Path("state/priors_btc_5m.json")
        assert c15.priors_path == Path("state/priors_btc_15m.json")
        assert c5.priors_path != c15.priors_path

    def test_slug_for_slot(self, yaml_path):
        cfg = load_configuration("btc_5m", path=yaml_path)
        assert cfg.slug_for_slot(1700000000) == "btc-updown-5m-1700000000"

    def test_dual_scan_offsets(self, yaml_path):
        cfg = load_configuration("btc_5m", path=yaml_path)
        assert cfg.early_warning_offset_s == 180  # 0.6 * 300
        assert cfg.confirmation_offset_s == 300   # 1.0 * 300

    def test_dual_scan_offsets_15m(self, yaml_path):
        cfg = load_configuration("btc_15m", path=yaml_path)
        assert cfg.early_warning_offset_s == 540  # 0.6 * 900
        assert cfg.confirmation_offset_s == 900
