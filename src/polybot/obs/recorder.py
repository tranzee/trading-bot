"""Phase 6 — per-session NDJSON trade recorder.

Each settled SlotPosition writes one JSON line to a daily log file under
`logs/positions_YYYYMMDD.ndjson`. The chart generator (Phase 9) reads these
files to build the equity curve.

Line schema (all string Decimals to preserve precision):
    {
      "ts_utc":       ISO-8601 timestamp,
      "market_id":    Polymarket condition_id or "" for paper,
      "token_id":     bought token,
      "direction":    "UP" | "DOWN",
      "shares":       str Decimal,
      "entry_price":  str Decimal,
      "fill_price":   str Decimal | null,
      "slot_end_ms":  int,
      "won":          bool | null,
      "gross_pnl_usd": str Decimal,
      "net_pnl_usd":  str Decimal,
      "paper":        bool
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polybot.engine.position import SlotPosition


class PositionRecorder:
    """Append one NDJSON line per settled position to a daily log file."""

    def __init__(self, log_dir: Path | str = "logs") -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def record_settlement(self, pos: "SlotPosition", *, market_id: str = "") -> None:
        now = datetime.now(timezone.utc)
        line = {
            "ts_utc": now.isoformat(),
            "market_id": market_id,
            "token_id": pos.token_id,
            "direction": pos.direction,
            "shares": str(pos.shares),
            "entry_price": str(pos.entry_price),
            "fill_price": str(pos.fill_price) if pos.fill_price is not None else None,
            "slot_end_ms": pos.slot_end_ms,
            "won": pos.settled_win,
            "gross_pnl_usd": str(pos.gross_pnl_usd),
            "net_pnl_usd": str(pos.net_pnl_usd),
            "paper": pos.paper,
        }
        log_path = self._log_dir / f"positions_{now.strftime('%Y%m%d')}.ndjson"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    @property
    def log_dir(self) -> Path:
        return self._log_dir
