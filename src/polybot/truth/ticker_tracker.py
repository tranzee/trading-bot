"""Multi-source BTC ticker — consensus and divergence health.

Aggregates ticks from {Binance, Coinbase, Chainlink}. Exposes:

    - get_price() -> Decimal | None : consensus (median; Binance breaks ties)
    - last_per_source -> dict[BtcSource, BtcTick]
    - divergence_bps -> Decimal | None : max pairwise divergence in bps of consensus
    - is_healthy() -> bool : per §6.1.4

Health (§6.1.4): False if
    - divergence_bps > threshold for > grace_window_s seconds, OR
    - any required source is stale > stale_timeout_s, OR
    - consensus jumped > 2% in < 1s (likely bad data spike).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from polybot.truth.models import BtcSource, BtcTick


@dataclass(slots=True, frozen=True)
class HealthStatus:
    healthy: bool
    reason: str
    divergence_bps: Decimal | None
    stale_sources: tuple[BtcSource, ...]


class TickerTracker:
    """Stateful aggregator. Feed it ticks via `on_tick(tick)`."""

    def __init__(
        self,
        *,
        required_sources: Iterable[BtcSource] | None = None,
        divergence_threshold_bps: Decimal = Decimal(50),
        divergence_grace_s: float = 2.0,
        stale_timeout_s: float = 5.0,
        spike_pct: Decimal = Decimal("0.02"),  # 2%
        spike_window_s: float = 1.0,
    ) -> None:
        self._required = tuple(required_sources or (BtcSource.BINANCE, BtcSource.COINBASE))
        self._divergence_threshold_bps = divergence_threshold_bps
        self._divergence_grace_s = divergence_grace_s
        self._stale_timeout_s = stale_timeout_s
        self._spike_pct = spike_pct
        self._spike_window_s = spike_window_s
        self._last_per_source: dict[BtcSource, BtcTick] = {}
        self._divergence_breach_since: float | None = None
        self._consensus_history: list[tuple[float, Decimal]] = []  # (rx_monotonic, price)

    # ----- ingestion ---------------------------------------------------------

    def on_tick(self, tick: BtcTick) -> None:
        prev = self._last_per_source.get(tick.source)
        # Be tolerant of out-of-order ticks: keep the newer rx-time tick.
        if prev is not None and tick.ts_ms < prev.ts_ms:
            return
        self._last_per_source[tick.source] = tick
        self._maybe_update_consensus_history()
        self._maybe_update_divergence_state()

    # ----- read accessors ----------------------------------------------------

    @property
    def last_per_source(self) -> dict[BtcSource, BtcTick]:
        return dict(self._last_per_source)

    def get_price(self) -> Decimal | None:
        """Median of available CEX sources (Binance, Coinbase). Chainlink is
        oracle truth and intentionally NOT used in trade-decision consensus."""
        prices = [
            t.price for s, t in self._last_per_source.items()
            if s in (BtcSource.BINANCE, BtcSource.COINBASE)
        ]
        if not prices:
            return None
        prices_sorted = sorted(prices)
        n = len(prices_sorted)
        if n % 2 == 1:
            return prices_sorted[n // 2]
        # Tie-break to Binance per §6.1.4 ("prioritizing Binance for tie-breaking")
        bin_tick = self._last_per_source.get(BtcSource.BINANCE)
        if bin_tick is not None:
            return bin_tick.price
        # Fallback: arithmetic mean
        return (prices_sorted[n // 2 - 1] + prices_sorted[n // 2]) / Decimal(2)

    def divergence_bps(self) -> Decimal | None:
        """Max pairwise divergence in bps of the consensus price.

        Considers only Binance and Coinbase (Chainlink intentionally excluded).
        STALE sources are skipped — comparing a fresh price to a 30s-old
        cached price during a WS reconnect would produce an artificial
        divergence spike. If fewer than 2 sources are fresh, returns None.
        """
        wall_now_ms = int(time.time() * 1000)
        stale_threshold_ms = int(self._stale_timeout_s * 1000)
        prices = [
            t.price for s, t in self._last_per_source.items()
            if s in (BtcSource.BINANCE, BtcSource.COINBASE)
            and wall_now_ms - t.ts_ms <= stale_threshold_ms
        ]
        if len(prices) < 2:
            return None
        consensus = self.get_price()
        if consensus is None or consensus <= 0:
            return None
        spread = max(prices) - min(prices)
        return (spread / consensus) * Decimal(10_000)

    def is_healthy(self, *, now_monotonic: float | None = None) -> HealthStatus:
        nm = now_monotonic if now_monotonic is not None else time.monotonic()
        wall_now_ms = int(time.time() * 1000)
        stale: list[BtcSource] = []
        for src in self._required:
            tick = self._last_per_source.get(src)
            if tick is None or wall_now_ms - tick.ts_ms > int(self._stale_timeout_s * 1000):
                stale.append(src)
        if stale:
            return HealthStatus(
                healthy=False,
                reason=f"stale sources: {', '.join(s.value for s in stale)}",
                divergence_bps=self.divergence_bps(),
                stale_sources=tuple(stale),
            )

        div = self.divergence_bps()
        if (
            div is not None
            and div > self._divergence_threshold_bps
            and self._divergence_breach_since is not None
            and nm - self._divergence_breach_since >= self._divergence_grace_s
        ):
            return HealthStatus(
                healthy=False,
                reason=f"divergence {div:.1f}bps > {self._divergence_threshold_bps}bps for {nm - self._divergence_breach_since:.1f}s",
                divergence_bps=div,
                stale_sources=(),
            )

        if self._has_recent_spike(nm):
            return HealthStatus(
                healthy=False,
                reason=f"consensus spike > {self._spike_pct * 100}% within {self._spike_window_s}s",
                divergence_bps=div,
                stale_sources=(),
            )

        return HealthStatus(healthy=True, reason="ok", divergence_bps=div, stale_sources=())

    # ----- internal helpers --------------------------------------------------

    def _maybe_update_divergence_state(self) -> None:
        nm = time.monotonic()
        div = self.divergence_bps()
        if div is None:
            self._divergence_breach_since = None
            return
        if div > self._divergence_threshold_bps:
            if self._divergence_breach_since is None:
                self._divergence_breach_since = nm
        else:
            self._divergence_breach_since = None

    def _maybe_update_consensus_history(self) -> None:
        cons = self.get_price()
        if cons is None:
            return
        nm = time.monotonic()
        self._consensus_history.append((nm, cons))
        # Keep only the spike window plus a small buffer
        cutoff = nm - self._spike_window_s - 0.5
        while self._consensus_history and self._consensus_history[0][0] < cutoff:
            self._consensus_history.pop(0)

    def _has_recent_spike(self, now_monotonic: float) -> bool:
        if len(self._consensus_history) < 2:
            return False
        window_start = now_monotonic - self._spike_window_s
        in_window = [p for (t, p) in self._consensus_history if t >= window_start]
        if len(in_window) < 2:
            return False
        lo, hi = min(in_window), max(in_window)
        if lo <= 0:
            return False
        return ((hi - lo) / lo) > self._spike_pct
