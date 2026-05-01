"""Slug -> condition_id resolution via Polymarket's Gamma API.

The slug is deterministic: `btc-updown-5m-{slot_end_unix_seconds}`. The
condition_id and token_ids are NOT — they are minted by Polymarket when the
market is created, typically a few minutes before slot open.

Strategy:
    - compute the next slot's slug (with a configurable offset)
    - poll Gamma until the slug resolves, with backoff (capped at 30s before
      slot open per §6.3.3)
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import httpx

from config import constants as K
from polybot.obs.logger import log
from polybot.obs.retry import CircuitBreaker, retry, with_timeout
from polybot.poly.client import PolyClient
from polybot.poly.order_dsl import MarketHandle


_GAMMA_CIRCUIT = CircuitBreaker(name="gamma", failure_threshold=8, cooldown_s=15.0)


def slot_boundary_ms(
    now_ms: int | None = None, *, window_ms: int = K.SLOT_DURATION_MS
) -> int:
    """Return the unix-ms of the slot END currently in progress for `window_ms`."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    bin_start = (now_ms // window_ms) * window_ms
    return bin_start + window_ms


def slug_for_slot(
    slot_end_ms: int, *, pattern: str = K.BTC_UPDOWN_5M_SLUG_TEMPLATE
) -> str:
    """Render the Polymarket slug. `pattern` must contain `{slot_end_ts}`."""
    return pattern.format(slot_end_ts=slot_end_ms // 1000)


@retry(
    attempts=2,
    base_delay_s=0.2,
    max_delay_s=2.0,
    retry_on=(httpx.HTTPError, asyncio.TimeoutError),
    circuit=_GAMMA_CIRCUIT,
    label="gamma.fetch_event",
)
async def _fetch_event_by_slug(slug: str, *, timeout_s: float = 5.0) -> dict[str, Any] | None:
    """One-shot Gamma query. Returns the event dict, or None if not found."""
    url = f"{K.POLYMARKET_GAMMA_API}/events"
    async with httpx.AsyncClient(timeout=timeout_s) as http:
        resp = await with_timeout(
            http.get(url, params={"slug": slug}),
            timeout_s,
            label="gamma_get",
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return dict(data[0])
        if isinstance(data, dict) and data.get("events"):
            events = data["events"]
            if events:
                return dict(events[0])
        return None


def _extract_token_ids(event: dict[str, Any]) -> tuple[str, str]:
    """Pull (UP, DOWN) token_ids out of a Gamma event payload.

    Gamma represents binary markets as a single market with two outcomes; the
    `clobTokenIds` field is a JSON-encoded list `[up_token_id, down_token_id]`
    (or a list of strings already parsed depending on Gamma's response shape).
    """
    markets = event.get("markets") or []
    if not markets:
        raise ValueError(f"event has no markets: {event.get('slug')}")
    market = markets[0]
    token_ids_field = market.get("clobTokenIds") or market.get("clob_token_ids")
    if isinstance(token_ids_field, str):
        import json

        try:
            token_ids_field = json.loads(token_ids_field)
        except Exception as exc:
            raise ValueError(f"clobTokenIds not parseable: {token_ids_field!r}") from exc
    if not (isinstance(token_ids_field, list) and len(token_ids_field) == 2):
        raise ValueError(f"clobTokenIds must be length-2 list; got {token_ids_field!r}")
    up_id = str(token_ids_field[0])
    down_id = str(token_ids_field[1])
    return up_id, down_id


def _extract_condition_id(event: dict[str, Any]) -> str:
    markets = event.get("markets") or []
    if not markets:
        raise ValueError("event has no markets")
    cid = markets[0].get("conditionId") or markets[0].get("condition_id")
    if not cid:
        raise ValueError("market has no conditionId")
    return str(cid)


async def resolve_slug(
    slug: str,
    *,
    poll_until_ms: int | None = None,
    poll_interval_s: float = 2.0,
) -> dict[str, Any]:
    """Poll Gamma until the slug resolves (or timeout).

    Per §6.3.3: a market is typically listed up to ~30s before slot open. We
    keep polling until either we get a hit or `poll_until_ms` expires.
    """
    deadline_ms = poll_until_ms if poll_until_ms is not None else int(time.time() * 1000) + 60_000
    while True:
        event = await _fetch_event_by_slug(slug)
        if event is not None:
            return event
        now_ms = int(time.time() * 1000)
        if now_ms >= deadline_ms:
            raise TimeoutError(f"market not listed by deadline: slug={slug}")
        log.info(
            "market_discovery: slug={} not yet listed; sleeping {:.1f}s",
            slug, poll_interval_s,
        )
        await asyncio.sleep(poll_interval_s)


async def resolve_next_slot(
    poly: PolyClient,
    *,
    slot_offset: int = 1,
    now_ms: int | None = None,
    window_ms: int = K.SLOT_DURATION_MS,
    slug_pattern: str = K.BTC_UPDOWN_5M_SLUG_TEMPLATE,
) -> MarketHandle:
    """Resolve the slug -> MarketHandle for the slot `slot_offset` ahead of now.

    `slot_offset=0` is the slot currently in progress; `slot_offset=1` is the
    next one (typical for pre-warm). `window_ms` and `slug_pattern` are taken
    from the active BotConfiguration in production.
    """
    base_end_ms = slot_boundary_ms(now_ms, window_ms=window_ms)
    target_end_ms = base_end_ms + slot_offset * window_ms
    target_start_ms = target_end_ms - window_ms
    slug = slug_for_slot(target_end_ms, pattern=slug_pattern)

    event = await resolve_slug(slug, poll_until_ms=target_start_ms + 30_000)

    condition_id = _extract_condition_id(event)
    up_id, down_id = _extract_token_ids(event)

    # Fetch tick size and neg_risk concurrently (per-token; both should agree
    # for a binary market but we query both for safety).
    tick_size_task = poly.get_tick_size(up_id)
    neg_risk_task = poly.get_neg_risk(up_id)
    tick_size, neg_risk = await asyncio.gather(tick_size_task, neg_risk_task)

    return MarketHandle(
        slug=slug,
        condition_id=condition_id,
        token_ids=(up_id, down_id),
        slot_start_ms=target_start_ms,
        slot_end_ms=target_end_ms,
        tick_size=Decimal(str(tick_size)),
        min_order_size=K.MIN_ORDER_SHARES,
        neg_risk=neg_risk,
    )
