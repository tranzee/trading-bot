"""Unit tests for the per-config Phase 3 gate validator."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from config.bot_configuration import load_configuration
from polybot.risk.gate_validator import (
    MAX_AGE_DAYS,
    MIN_AGGREGATE,
    MIN_BOOTSTRAP_CI_5PCT,
    MIN_SAMPLE_SIZE,
    validate_priors_for_config,
)


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch):
    yaml_path = tmp_path / "configurations.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "configurations": {
            "test_5m": {
                "asset": "BTC",
                "window_seconds": 300,
                "polymarket_slug_pattern": "p-{slot_end_ts}",
                "binance_stream_pattern": "/x",
                "coinbase_product": "BTC-USD",
                "binance_vision_pattern": "BTCUSDT-5m",
            }
        }
    }), encoding="utf-8")
    cfg = load_configuration("test_5m", path=yaml_path)
    # Redirect priors_path into tmp_path
    monkeypatch.setattr(
        type(cfg), "priors_path",
        property(lambda self: tmp_path / f"priors_{self.name}.json"),
    )
    return cfg


def _write_priors(cfg, **kwargs) -> Path:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aggregate_continuation": 0.55,
        "bootstrap_ci_5pct": 0.52,
        "sample_size": 250,
        **kwargs,
    }
    cfg.priors_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.priors_path.write_text(json.dumps(payload), encoding="utf-8")
    return cfg.priors_path


def test_missing_file_fails(cfg):
    r = validate_priors_for_config(cfg)
    assert not r.passed
    assert "missing" in r.reason


def test_passing_priors_pass(cfg):
    _write_priors(cfg)
    r = validate_priors_for_config(cfg)
    assert r.passed
    assert r.aggregate == 0.55


def test_low_aggregate_fails(cfg):
    _write_priors(cfg, aggregate_continuation=0.50)
    r = validate_priors_for_config(cfg)
    assert not r.passed
    assert "aggregate" in r.reason


def test_low_ci5_fails(cfg):
    _write_priors(cfg, bootstrap_ci_5pct=0.40)
    r = validate_priors_for_config(cfg)
    assert not r.passed
    assert "ci_5pct" in r.reason or "ci5" in r.reason.lower()


def test_small_sample_fails(cfg):
    _write_priors(cfg, sample_size=100)
    r = validate_priors_for_config(cfg)
    assert not r.passed
    assert str(MIN_SAMPLE_SIZE) in r.reason


def test_stale_fails(cfg):
    old = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS + 5)).isoformat()
    _write_priors(cfg, generated_at=old)
    r = validate_priors_for_config(cfg)
    assert not r.passed
    assert "age" in r.reason


def test_thresholds_match_blueprint():
    assert MIN_AGGREGATE == 0.54
    assert MIN_BOOTSTRAP_CI_5PCT == 0.51
    assert MIN_SAMPLE_SIZE == 200
    assert MAX_AGE_DAYS == 60
