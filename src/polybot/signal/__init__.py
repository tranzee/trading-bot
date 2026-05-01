"""Signal layer — price action engine.

The user's structural price-action methodology mechanized:
    - 4-tier liquidity hierarchy (MAIN / SLQ / TLQ / ILQ)
    - EPA / IPA efficiency tracking
    - Malaysian SnD zone rejection
    - 6 invalidation kill-switches
    - 3-tier alert system (Alert 1, Alert 2 UI-only, Alert 3 execution)
    - Cold-start bootstrap (§6.2.9)

Implementation is Phase 3 — the core of the bot. See MASTER_BLUEPRINT.md §6.2.
"""
