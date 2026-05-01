"""Startup banner — one-screen safety check on every boot.

Prints the resolved Polymarket host and warns if it's misaligned with the
April 28, 2026 ~11:00 UTC V2 cutover:

    - Before cutover: should be clob-v2.polymarket.com (testnet).
      Using clob.polymarket.com pre-cutover talks to V1, which is forbidden.
    - After cutover (with a 1-hour transition grace until 12:00 UTC):
      should be clob.polymarket.com (production V2).
      Lingering on clob-v2.polymarket.com after cutover means stale testnet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from config import constants as K
from config.settings import RunMode, Settings

PRECUTOVER_HOST: str = K.CLOB_V2_PRECUTOVER_HOST
PRODUCTION_HOST: str = K.CLOB_V2_PRODUCTION_HOST

#: 1-hour grace window after cutover (ends 2026-04-28 12:00 UTC) during which
#: either host is acceptable while infra rolls forward.
CUTOVER_GRACE_MS: int = 60 * 60 * 1000


@dataclass(frozen=True)
class HostBanner:
    host: str
    now_ms: int
    cutover_ms: int
    is_precutover_window: bool
    is_postcutover_window: bool
    is_warning: bool
    warning: str | None
    text: str


def _now_ms() -> int:
    return int(time.time() * 1000)


def evaluate_host(host: str, *, now_ms: int | None = None) -> HostBanner:
    """Pure function: classify the host vs. the cutover. Used by the banner and tests."""
    ts = _now_ms() if now_ms is None else now_ms
    pre = ts < K.CLOB_V2_CUTOVER_MS
    post = ts >= K.CLOB_V2_CUTOVER_MS + CUTOVER_GRACE_MS
    in_grace = K.CLOB_V2_CUTOVER_MS <= ts < K.CLOB_V2_CUTOVER_MS + CUTOVER_GRACE_MS

    warning: str | None = None
    if pre and host == PRODUCTION_HOST:
        warning = (
            "host=clob.polymarket.com BEFORE cutover (2026-04-28 11:00 UTC). "
            "Pre-cutover this URL serves V1, which is forbidden. "
            "Switch to clob-v2.polymarket.com until cutover."
        )
    elif post and host == PRECUTOVER_HOST:
        warning = (
            "host=clob-v2.polymarket.com AFTER cutover grace (2026-04-28 12:00 UTC). "
            "Production V2 has taken over clob.polymarket.com. "
            "Switch to clob.polymarket.com to talk to live V2."
        )
    elif host not in (PRECUTOVER_HOST, PRODUCTION_HOST):
        warning = f"host={host!r} is neither the pre-cutover nor the production V2 host."

    text = _format_banner(host, ts, pre=pre, post=post, in_grace=in_grace, warning=warning)
    return HostBanner(
        host=host,
        now_ms=ts,
        cutover_ms=K.CLOB_V2_CUTOVER_MS,
        is_precutover_window=pre,
        is_postcutover_window=post,
        is_warning=warning is not None,
        warning=warning,
        text=text,
    )


def _format_banner(
    host: str, now_ms: int, *, pre: bool, post: bool, in_grace: bool, warning: str | None
) -> str:
    if pre:
        phase = "PRE-CUTOVER (V1 still authoritative; V2 testnet is the only valid V2 endpoint)"
    elif in_grace:
        phase = "CUTOVER GRACE (first hour after V2 takeover; either host accepted)"
    else:
        phase = "POST-CUTOVER (V2 production)"

    delta_ms = K.CLOB_V2_CUTOVER_MS - now_ms
    if delta_ms > 0:
        delta_h = delta_ms / 3_600_000
        delta_str = f"in {delta_h:.1f}h"
    else:
        delta_h = -delta_ms / 3_600_000
        delta_str = f"{delta_h:.1f}h ago"

    bar = "=" * 72
    lines = [
        bar,
        " polybot startup",
        bar,
        f"  Polymarket host : {host}",
        f"  Cutover phase   : {phase}",
        f"  Cutover at      : 2026-04-28 11:00:00 UTC ({delta_str})",
    ]
    if warning:
        lines += [
            "",
            f"  !! WARNING: {warning}",
        ]
    lines.append(bar)
    return "\n".join(lines)


def render_banner(settings: Settings) -> HostBanner:
    """Build the banner for the loaded settings."""
    banner = evaluate_host(settings.POLYMARKET_HOST)
    return banner


def print_banner(settings: Settings) -> HostBanner:
    """Render and print the banner; return the data for callers that want to act on it."""
    banner = render_banner(settings)
    extra = (
        f"  Run mode        : {settings.RUN_MODE.value}\n"
        f"  Strategy        : {settings.STRATEGY}\n"
        f"  Wallet balance  : ${settings.WALLET_BALANCE} (paper) "
        f"  Per-trade cap   : ${settings.MAX_PER_TRADE_USD}\n"
    )
    # Splice extra lines just above the closing bar
    text_lines = banner.text.split("\n")
    closing = text_lines[-1]
    text_lines = text_lines[:-1] + extra.rstrip().split("\n") + [closing]
    print("\n".join(text_lines))
    if banner.is_warning and settings.RUN_MODE is RunMode.LIVE:
        # In live mode, a host mismatch is a fatal misconfiguration — we want the
        # operator to fix and re-launch rather than silently mis-route.
        raise SystemExit(2)
    return banner
