"""Unit tests for chart_generator (Phase 9)."""

import json
from pathlib import Path

import pytest

from polybot.obs.chart_generator import generate_chart


def _write_ndjson(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_generates_html(tmp_path):
    log = tmp_path / "positions_20260430.ndjson"
    _write_ndjson(log, [
        {"ts_utc": "2026-04-30T10:00:00Z", "direction": "DOWN", "net_pnl_usd": "2.50", "won": True},
        {"ts_utc": "2026-04-30T10:05:00Z", "direction": "DOWN", "net_pnl_usd": "-2.45", "won": False},
        {"ts_utc": "2026-04-30T10:10:00Z", "direction": "UP",   "net_pnl_usd": "2.50", "won": True},
    ])
    out = tmp_path / "chart.html"
    result = generate_chart(log, out)
    assert result == out
    html = out.read_text(encoding="utf-8")
    assert "polybot" in html
    assert "chart.js" in html
    assert "2.55" in html  # net pnl (2.50 - 2.45 + 2.50 = 2.55) appears in stats


def test_raises_on_empty_log(tmp_path):
    log = tmp_path / "empty.ndjson"
    log.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="No records"):
        generate_chart(log, tmp_path / "out.html")


def test_equity_curve_accumulates(tmp_path):
    log = tmp_path / "pos.ndjson"
    _write_ndjson(log, [
        {"ts_utc": "2026-04-30T10:00:00Z", "net_pnl_usd": "1.0", "won": True},
        {"ts_utc": "2026-04-30T10:05:00Z", "net_pnl_usd": "1.0", "won": True},
    ])
    out = tmp_path / "chart.html"
    generate_chart(log, out)
    html = out.read_text(encoding="utf-8")
    # Final cumulative PnL = 2.0 appears in equity array
    assert "2.0" in html
