"""Historical BTC kline loader from Binance Vision (free historical archive).

Source: https://data.binance.vision/data/spot/daily/klines/BTCUSDT/5m/
Each ZIP contains a single CSV: BTCUSDT-5m-YYYY-MM-DD.csv with columns:
    open_time, open, high, low, close, volume, close_time, quote_asset_volume,
    number_of_trades, taker_buy_base_volume, taker_buy_quote_volume, ignore
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import httpx

from polybot.obs.logger import log
from polybot.truth.models import Candle


BINANCE_VISION_HOST = "https://data.binance.vision/data/spot/daily/klines"


def _vision_url(symbol: str, window: str, d: date) -> str:
    return f"{BINANCE_VISION_HOST}/{symbol}/{window}/{symbol}-{window}-{d.isoformat()}.zip"


def _local_path(symbol: str, window: str, d: date, cache_dir: Path) -> Path:
    return cache_dir / f"{symbol}-{window}-{d.isoformat()}.zip"


def _download_one(
    d: date, cache_dir: Path, *, symbol: str = "BTCUSDT", window: str = "5m"
) -> Path | None:
    """Download the ZIP for date d to cache_dir. Returns local path, or None if 404."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = _local_path(symbol, window, d, cache_dir)
    if target.exists() and target.stat().st_size > 0:
        return target
    url = _vision_url(symbol, window, d)
    try:
        with httpx.Client(timeout=30) as http:
            r = http.get(url)
            if r.status_code == 404:
                log.info("data_loader: 404 (no data yet) {}", d.isoformat())
                return None
            r.raise_for_status()
            target.write_bytes(r.content)
            return target
    except Exception as exc:
        log.warning("data_loader: download failed for {}: {}", d.isoformat(), exc)
        return None


def _read_zip(path: Path) -> Iterator[Candle]:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if not names:
            return
        with zf.open(names[0]) as f:
            text = io.TextIOWrapper(f, encoding="utf-8")
            reader = csv.reader(text)
            for row in reader:
                if not row or row[0].lstrip("-").isdigit() is False:
                    # Header row or empty; skip
                    if row and row[0] == "open_time":
                        continue
                # Binance Vision rows are pure data (no header), but be safe
                try:
                    open_time = int(row[0])
                except ValueError:
                    continue
                # Binance Vision switched to microsecond timestamps in mid-2023.
                # Detect: ms timestamps are 13 digits in the 2020s; us are 16.
                if open_time > 10**14:
                    open_time //= 1000
                yield Candle(
                    ts_ms=open_time,
                    open=Decimal(row[1]),
                    high=Decimal(row[2]),
                    low=Decimal(row[3]),
                    close=Decimal(row[4]),
                    volume=Decimal(row[5]),
                    n_ticks=int(float(row[8])) if len(row) >= 9 else 0,
                )


def load_btc_5m_range(
    start: date,
    end: date,
    *,
    cache_dir: Path = Path("data/binance_5m"),
    symbol: str = "BTCUSDT",
    window: str = "5m",
) -> list[Candle]:
    """Load all OHLCV klines for `symbol`/`window` from `start`..`end` (inclusive)."""
    if end < start:
        raise ValueError("end must be >= start")
    candles: list[Candle] = []
    d = start
    while d <= end:
        path = _download_one(d, cache_dir, symbol=symbol, window=window)
        if path is not None:
            for c in _read_zip(path):
                candles.append(c)
        d += timedelta(days=1)
    candles.sort(key=lambda c: c.ts_ms)
    log.info(
        "data_loader: loaded {} candles for {} {} {}..{}",
        len(candles), symbol, window, start, end,
    )
    return candles


def load_recent_days(
    days: int,
    *,
    end: date | None = None,
    cache_dir: Path | None = None,
    symbol: str = "BTCUSDT",
    window: str = "5m",
) -> list[Candle]:
    """Load the most recent `days` of data ending at `end` (default: today).

    Cache directory defaults to `data/binance_{window}` so different window
    sizes don't collide in the same cache directory.
    """
    end = end or datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    cache = cache_dir or Path(f"data/binance_{window}")
    return load_btc_5m_range(start, end, cache_dir=cache, symbol=symbol, window=window)


def load_recent_days_for_config(days: int, cfg, *, end: date | None = None) -> list[Candle]:
    """Convenience: load recent days for a BotConfiguration.

    Derives the Binance Vision symbol/window from `cfg.binance_vision_pattern`
    (e.g. ``BTCUSDT-5m``) so each named config caches in its own directory.
    """
    pattern = cfg.binance_vision_pattern
    if "-" not in pattern:
        raise ValueError(f"binance_vision_pattern must be 'SYMBOL-WINDOW': {pattern!r}")
    symbol, window = pattern.split("-", 1)
    return load_recent_days(days, end=end, symbol=symbol, window=window)
