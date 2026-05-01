"""Backtester — historical replay over BTC ticks + Polymarket trades.

The Phase 3 statistical gate (§7) is computed here:
    - 3.B aggregate continuation probability >= 0.54 over >= 200 signals
      with bootstrap 5th-percentile >= 0.51
    - 3.C cost-adjusted EV >= $0.05 per signal on $5 position size

Output: state/continuation_priors.json (consumed by Signal.confidence in §6.2.8).

Phase 3 (minimal) / Phase 8 (full). See MASTER_BLUEPRINT.md §6.9.
"""
