"""Persistence — SQLite for structured records, JSON snapshots for in-memory state.

Crash recovery: reconcile pending orders against exchange on startup.
Phase 6 deliverable. See MASTER_BLUEPRINT.md §6.6.
"""
