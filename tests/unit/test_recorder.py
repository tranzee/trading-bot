"""Unit tests for PositionRecorder (Phase 6)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from polybot.engine.position import SlotPosition
from polybot.obs.recorder import PositionRecorder


def _settled_pos(won: bool = True) -> SlotPosition:
    p = SlotPosition(
        direction="DOWN",
        token_id="tok_xyz",
        shares=Decimal("10"),
        entry_price=Decimal("0.49"),
        slot_end_ms=1_700_000_300_000,
        paper=True,
    )
    p.record_fill(Decimal("0.49"))
    p.record_settlement(won=won, maker_rebate_usd=Decimal("0.005"))
    return p


def test_record_creates_file(tmp_path: Path):
    rec = PositionRecorder(tmp_path)
    pos = _settled_pos(won=True)
    rec.record_settlement(pos, market_id="cond_abc")

    ndjson_files = list(tmp_path.glob("positions_*.ndjson"))
    assert len(ndjson_files) == 1

    line = json.loads(ndjson_files[0].read_text(encoding="utf-8").strip())
    assert line["direction"] == "DOWN"
    assert line["won"] is True
    assert line["market_id"] == "cond_abc"
    assert line["paper"] is True
    assert line["gross_pnl_usd"] == "5.10"
    assert line["net_pnl_usd"] == "5.105"


def test_appends_multiple_records(tmp_path: Path):
    rec = PositionRecorder(tmp_path)
    rec.record_settlement(_settled_pos(won=True))
    rec.record_settlement(_settled_pos(won=False))

    ndjson_files = list(tmp_path.glob("positions_*.ndjson"))
    lines = ndjson_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["won"] is True
    assert json.loads(lines[1])["won"] is False


def test_fill_price_null_when_not_filled(tmp_path: Path):
    p = SlotPosition(
        direction="UP",
        token_id="tok_up",
        shares=Decimal("5"),
        entry_price=Decimal("0.51"),
        slot_end_ms=1_700_000_300_000,
        paper=True,
    )
    # Not filled — no record_fill call
    rec = PositionRecorder(tmp_path)
    rec.record_settlement(p)
    ndjson_files = list(tmp_path.glob("positions_*.ndjson"))
    line = json.loads(ndjson_files[0].read_text(encoding="utf-8").strip())
    assert line["fill_price"] is None
