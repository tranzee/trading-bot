"""Test payload parsing of Binance, Coinbase, RTDS WS messages (offline)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from polybot.truth.binance_ws import BinanceWs
from polybot.truth.chainlink_rtds import ChainlinkRtds
from polybot.truth.coinbase_ws import CoinbaseWs, _parse_iso_to_ms
from polybot.truth.models import BtcSource, BtcKlineClose, BtcTick


@pytest.mark.asyncio
async def test_binance_trade_payload_emits_tick() -> None:
    ws = BinanceWs()
    seen: list[BtcTick] = []
    ws.add_tick_handler(lambda t: seen.append(t))

    raw = json.dumps(
        {
            "stream": "btcusdt@trade",
            "data": {
                "e": "trade",
                "E": 1_777_167_000_000,
                "T": 1_777_167_000_000,
                "p": "100250.50",
                "q": "0.05",
                "s": "BTCUSDT",
            },
        }
    )
    await ws._handle_raw(raw)
    assert len(seen) == 1
    t = seen[0]
    assert t.source == BtcSource.BINANCE
    assert t.price == Decimal("100250.50")
    assert t.volume == Decimal("0.05")
    assert t.ts_ms == 1_777_167_000_000


@pytest.mark.asyncio
async def test_binance_kline_final_emits_kline_close() -> None:
    ws = BinanceWs()
    seen: list[BtcKlineClose] = []
    ws.add_kline_handler(lambda k: seen.append(k))

    raw = json.dumps(
        {
            "stream": "btcusdt@kline_5m",
            "data": {
                "e": "kline",
                "k": {
                    "t": 1_777_167_000_000,
                    "o": "100100",
                    "h": "100250",
                    "l": "99900",
                    "c": "100200",
                    "v": "12.345",
                    "x": True,
                },
            },
        }
    )
    await ws._handle_raw(raw)
    assert len(seen) == 1
    k = seen[0]
    assert k.is_final is True
    assert k.open == Decimal("100100")
    assert k.high == Decimal("100250")
    assert k.low == Decimal("99900")
    assert k.close == Decimal("100200")
    assert k.volume == Decimal("12.345")


@pytest.mark.asyncio
async def test_binance_ignores_malformed_payload() -> None:
    ws = BinanceWs()
    seen: list[BtcTick] = []
    ws.add_tick_handler(lambda t: seen.append(t))
    await ws._handle_raw("not-json")
    await ws._handle_raw(json.dumps({"unrelated": True}))
    assert seen == []


@pytest.mark.asyncio
async def test_coinbase_market_trades_payload() -> None:
    ws = CoinbaseWs()
    seen: list[BtcTick] = []
    ws.add_tick_handler(lambda t: seen.append(t))
    raw = json.dumps(
        {
            "channel": "market_trades",
            "events": [
                {
                    "trades": [
                        {
                            "trade_id": "1",
                            "product_id": "BTC-USD",
                            "price": "100050.0",
                            "size": "0.1",
                            "side": "BUY",
                            "time": "2026-04-26T01:23:45.678Z",
                        }
                    ]
                }
            ],
        }
    )
    await ws._handle_raw(raw)
    assert len(seen) == 1
    assert seen[0].source == BtcSource.COINBASE
    assert seen[0].price == Decimal("100050.0")


def test_iso_to_ms_handles_various_formats() -> None:
    a = _parse_iso_to_ms("2026-04-26T01:23:45Z")
    b = _parse_iso_to_ms("2026-04-26T01:23:45.678Z")
    c = _parse_iso_to_ms("2026-04-26T01:23:45.123456789Z")
    assert b > a  # the .678 is later within the same second
    # 2026-04-26T01:23:45.678Z -> exact unix-ms
    assert b == 1_777_166_625_678
    # nanosecond input must not crash; should be truncated to micros
    assert c >= b - 1000  # same second; truncation may shift sub-ms


@pytest.mark.asyncio
async def test_rtds_btc_payload_emits_tick() -> None:
    rtds = ChainlinkRtds()
    seen: list[BtcTick] = []
    rtds.add_tick_handler(lambda t: seen.append(t))

    raw = json.dumps(
        {
            "channel": "crypto_prices",
            "symbol": "BTC",
            "price": "100100",
            "ts": 1_777_167_000_000,
        }
    )
    await rtds._handle_raw(raw)
    assert len(seen) == 1
    assert seen[0].source == BtcSource.CHAINLINK
    assert seen[0].price == Decimal("100100")


@pytest.mark.asyncio
async def test_rtds_ignores_non_btc() -> None:
    rtds = ChainlinkRtds()
    seen: list[BtcTick] = []
    rtds.add_tick_handler(lambda t: seen.append(t))
    raw = json.dumps({"symbol": "ETH", "price": "3000"})
    await rtds._handle_raw(raw)
    assert seen == []
