"""EventFilter — static YAML calendar w/ staleness + fail-closed."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from polybot.signal.event_filter import (
    EventFilter,
    EventFilterParams,
    write_skeleton_calendar,
)


def test_missing_file_is_unhealthy_and_blocks_when_fail_closed(tmp_path: Path) -> None:
    p = tmp_path / "calendar.yaml"
    f = EventFilter(p, register_sighup=False)
    assert not f.status.healthy
    blocked, reason = f.is_blocked_at(dt.datetime.now(dt.timezone.utc))
    assert blocked
    assert "unhealthy" in (reason or "")


def test_fresh_calendar_is_healthy(tmp_path: Path) -> None:
    p = tmp_path / "calendar.yaml"
    today = dt.date.today()
    write_skeleton_calendar(p, [
        {
            "date": today.isoformat(),
            "time_utc": "12:30",
            "event_name": "CPI",
            "currency": "USD",
            "impact": "high",
            "source_url": "https://example.com",
        },
    ])
    f = EventFilter(p, register_sighup=False)
    assert f.status.healthy
    assert f.status.n_events_loaded == 1


def test_stale_calendar_unhealthy(tmp_path: Path) -> None:
    p = tmp_path / "calendar.yaml"
    old = dt.date.today() - dt.timedelta(days=30)
    write_skeleton_calendar(p, [
        {
            "date": old.isoformat(), "time_utc": "12:30",
            "event_name": "CPI", "currency": "USD",
            "impact": "high", "source_url": "x",
        },
    ])
    f = EventFilter(p, register_sighup=False)
    assert not f.status.healthy
    assert "stale" in f.status.reason.lower() or "old" in f.status.reason.lower()


def test_blocks_inside_event_window(tmp_path: Path) -> None:
    p = tmp_path / "calendar.yaml"
    today = dt.date.today()
    write_skeleton_calendar(p, [
        {
            "date": today.isoformat(), "time_utc": "12:30",
            "event_name": "CPI", "currency": "USD",
            "impact": "high", "source_url": "x",
        },
    ])
    f = EventFilter(p, register_sighup=False)
    inside = dt.datetime.combine(today, dt.time(12, 28), tzinfo=dt.timezone.utc)
    outside = dt.datetime.combine(today, dt.time(20, 0), tzinfo=dt.timezone.utc)
    blocked_in, _ = f.is_blocked_at(inside)
    blocked_out, _ = f.is_blocked_at(outside)
    assert blocked_in
    assert not blocked_out


def test_skips_low_impact_events(tmp_path: Path) -> None:
    p = tmp_path / "calendar.yaml"
    today = dt.date.today()
    write_skeleton_calendar(p, [
        {
            "date": today.isoformat(), "time_utc": "12:30",
            "event_name": "Random", "currency": "USD",
            "impact": "low", "source_url": "x",
        },
    ])
    params = EventFilterParams(blocked_event_impact=("high",))
    f = EventFilter(p, params=params, register_sighup=False)
    inside = dt.datetime.combine(today, dt.time(12, 28), tzinfo=dt.timezone.utc)
    blocked, _ = f.is_blocked_at(inside)
    assert not blocked


def test_fail_open_when_configured(tmp_path: Path) -> None:
    p = tmp_path / "calendar.yaml"  # missing
    params = EventFilterParams(fail_closed_on_unhealthy=False)
    f = EventFilter(p, params=params, register_sighup=False)
    blocked, _ = f.is_blocked_at(dt.datetime.now(dt.timezone.utc))
    assert not blocked  # explicit fail-open
