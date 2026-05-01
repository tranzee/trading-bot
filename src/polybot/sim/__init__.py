"""Simulator — drop-in replacement for poly.client with realistic paper fills.

Subscribes to the REAL Polymarket book WS and tracks virtual orders against
the real book with injected latency, partial fills, and 1% rejection rate.
Phase 7 deliverable. See MASTER_BLUEPRINT.md §6.8.
"""
