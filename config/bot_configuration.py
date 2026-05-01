"""Named bot configurations (multi-config refactor).

A `BotConfiguration` bundles every parameter that varies between trading
windows or assets — asset symbol, candle window seconds, exchange stream
identifiers, Polymarket slug pattern, HTF window, freshness half-life, and
dual-scan timing. Strategy parameters (pivot lookback, EMA periods, signal
confidence thresholds) are NOT part of the configuration: same strategy,
parameterized differently.

Configurations live in `config/configurations.yaml`. Each entry is keyed
by a short name like `btc_5m`, `btc_15m`, `eth_5m`. The CLI accepts
`--config <name>` on every command and resolves to one of these entries.

State files are scoped per configuration:
    state/priors_{config_name}.json
    state/early_bird_{config_name}.json

so that a btc_5m run can never silently consume btc_15m priors.

Default ratios (overridable per-config):
    htf_window_seconds        = 12 × window_seconds
    freshness_half_life_min   = 6  × (window_seconds / 60)
    dual_scan_early_warning   = 0.6 × window_seconds
    dual_scan_confirmation    = 1.0 × window_seconds
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


_CONFIGURATIONS_YAML = Path("config/configurations.yaml")


@dataclass(frozen=True)
class BotConfiguration:
    """Bundle of asset/window/timing parameters for one named configuration."""

    name: str
    asset: str
    window_seconds: int
    polymarket_slug_pattern: str
    binance_stream_pattern: str
    coinbase_product: str
    binance_vision_pattern: str  # for historical loader

    # Derived/overridable timing
    htf_window_seconds: int
    freshness_half_life_min: Decimal
    dual_scan_early_warning_pct: Decimal = Decimal("0.6")
    dual_scan_confirmation_pct: Decimal = Decimal("1.0")

    @property
    def window_ms(self) -> int:
        return self.window_seconds * 1000

    @property
    def htf_window_ms(self) -> int:
        return self.htf_window_seconds * 1000

    @property
    def early_warning_offset_s(self) -> int:
        return int(self.window_seconds * float(self.dual_scan_early_warning_pct))

    @property
    def confirmation_offset_s(self) -> int:
        return int(self.window_seconds * float(self.dual_scan_confirmation_pct))

    @property
    def priors_path(self) -> Path:
        return Path(f"state/priors_{self.name}.json")

    @property
    def early_bird_path(self) -> Path:
        return Path(f"state/early_bird_{self.name}.json")

    @property
    def guard_state_path(self) -> Path:
        return Path(f"state/guard_state_{self.name}.json")

    def slug_for_slot(self, slot_end_ts: int) -> str:
        """Render the Polymarket slug for a slot ending at slot_end_ts (unix sec)."""
        return self.polymarket_slug_pattern.format(slot_end_ts=slot_end_ts)


def _apply_defaults(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Fill in derived defaults if a configuration entry omits them."""
    window_s = int(raw["window_seconds"])
    out = dict(raw)
    out["name"] = name
    out.setdefault("htf_window_seconds", window_s * 12)
    out.setdefault("freshness_half_life_min", Decimal(str(6 * window_s / 60)))
    if "freshness_half_life_min" in raw:
        out["freshness_half_life_min"] = Decimal(str(raw["freshness_half_life_min"]))
    out.setdefault("dual_scan_early_warning_pct", Decimal("0.6"))
    out.setdefault("dual_scan_confirmation_pct", Decimal("1.0"))
    if "dual_scan_early_warning_pct" in raw:
        out["dual_scan_early_warning_pct"] = Decimal(str(raw["dual_scan_early_warning_pct"]))
    if "dual_scan_confirmation_pct" in raw:
        out["dual_scan_confirmation_pct"] = Decimal(str(raw["dual_scan_confirmation_pct"]))
    return out


def load_all_configurations(path: Path | str = _CONFIGURATIONS_YAML) -> dict[str, BotConfiguration]:
    """Load every configuration entry from the YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"configurations file missing: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if "configurations" not in raw:
        raise ValueError(f"{p}: missing top-level 'configurations' key")
    out: dict[str, BotConfiguration] = {}
    for name, body in raw["configurations"].items():
        if not isinstance(body, dict):
            raise ValueError(f"{p}: configuration {name!r} must be a mapping")
        merged = _apply_defaults(name, body)
        out[name] = BotConfiguration(**merged)
    return out


def load_configuration(
    name: str, *, path: Path | str = _CONFIGURATIONS_YAML
) -> BotConfiguration:
    """Load one named configuration. Raises KeyError if not found."""
    all_cfgs = load_all_configurations(path)
    if name not in all_cfgs:
        raise KeyError(
            f"unknown configuration {name!r}; available: {sorted(all_cfgs)}"
        )
    return all_cfgs[name]
