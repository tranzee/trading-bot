"""Economic-event filter (§1.5.7).

Static YAML, operator-maintained. Schema per row:
    date: 'YYYY-MM-DD'
    time_utc: 'HH:MM'
    event_name: str
    currency: str   (e.g. 'USD')
    impact: 'high' | 'medium' | 'low'
    source_url: str

Reload: SIGHUP on POSIX; programmatic `reload()` everywhere; optional
file-mtime poll. Staleness: if (most recent event date) - today > 14 days
in the past, OR file missing -> healthy=False.

Default behavior: fail_closed_on_unhealthy=True -> entries blocked when
unhealthy.
"""

from __future__ import annotations

import datetime as dt
import signal as _signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from polybot.obs.logger import log


@dataclass(frozen=True)
class CalendarEvent:
    when_utc: dt.datetime
    event_name: str
    currency: str
    impact: str
    source_url: str


@dataclass
class EventFilterParams:
    block_minutes_before_event: int = 5
    block_minutes_after_event: int = 10
    blocked_event_impact: tuple[str, ...] = ("high",)
    blocked_currencies: tuple[str, ...] = ("USD",)
    fail_closed_on_unhealthy: bool = True
    staleness_max_days: int = 14


@dataclass
class EventFilterStatus:
    healthy: bool
    reason: str
    file_path: Path
    file_exists: bool
    most_recent_event_date: dt.date | None
    n_events_loaded: int


class EventFilter:
    """Static-YAML calendar with SIGHUP reload + staleness detection."""

    def __init__(
        self,
        path: Path,
        *,
        params: EventFilterParams | None = None,
        register_sighup: bool = True,
    ) -> None:
        self._path = Path(path)
        self._params = params or EventFilterParams()
        self._events: list[CalendarEvent] = []
        self._status = EventFilterStatus(
            healthy=False,
            reason="not yet loaded",
            file_path=self._path,
            file_exists=False,
            most_recent_event_date=None,
            n_events_loaded=0,
        )
        self.reload()
        if register_sighup and sys.platform != "win32":
            try:
                _signal.signal(_signal.SIGHUP, self._on_sighup)
            except (ValueError, OSError):
                # SIGHUP may not be settable in the current context (e.g. thread)
                pass

    @property
    def status(self) -> EventFilterStatus:
        return self._status

    @property
    def events(self) -> list[CalendarEvent]:
        return list(self._events)

    def _on_sighup(self, *_args: object) -> None:
        log.info("event_filter: SIGHUP received; reloading {}", self._path)
        self.reload()

    def reload(self) -> None:
        if not self._path.exists():
            self._events = []
            self._status = EventFilterStatus(
                healthy=False,
                reason=f"calendar file missing: {self._path}",
                file_path=self._path,
                file_exists=False,
                most_recent_event_date=None,
                n_events_loaded=0,
            )
            log.warning("event_filter: calendar missing: {}", self._path)
            return
        try:
            text = self._path.read_text(encoding="utf-8")
            raw = yaml.safe_load(text) or []
            if not isinstance(raw, list):
                raise ValueError("calendar must be a list of events")
            events = [_parse_row(r) for r in raw if isinstance(r, dict)]
        except Exception as exc:
            self._events = []
            self._status = EventFilterStatus(
                healthy=False,
                reason=f"parse error: {exc}",
                file_path=self._path,
                file_exists=True,
                most_recent_event_date=None,
                n_events_loaded=0,
            )
            log.warning("event_filter: parse error: {}", exc)
            return

        self._events = sorted(events, key=lambda e: e.when_utc)
        most_recent = self._events[-1].when_utc.date() if self._events else None
        today = dt.date.today()
        stale_reason: str | None = None
        if most_recent is None:
            stale_reason = "calendar empty"
        else:
            age_days = (today - most_recent).days
            if age_days > self._params.staleness_max_days:
                stale_reason = (
                    f"most recent event {most_recent.isoformat()} is {age_days}d old "
                    f"(>{self._params.staleness_max_days}d threshold)"
                )
        if stale_reason is not None:
            self._status = EventFilterStatus(
                healthy=False,
                reason=stale_reason,
                file_path=self._path,
                file_exists=True,
                most_recent_event_date=most_recent,
                n_events_loaded=len(self._events),
            )
            log.warning("event_filter: stale: {}", stale_reason)
            return
        self._status = EventFilterStatus(
            healthy=True,
            reason="ok",
            file_path=self._path,
            file_exists=True,
            most_recent_event_date=most_recent,
            n_events_loaded=len(self._events),
        )
        log.info(
            "event_filter: loaded {} events; most recent = {}",
            len(self._events), most_recent,
        )

    def is_blocked_at(self, ts_utc: dt.datetime) -> tuple[bool, str | None]:
        """Return (blocked, reason). Blocked iff:
        - filter unhealthy AND fail_closed_on_unhealthy=True, OR
        - ts_utc lies within [event - before, event + after] for any matching event.
        """
        if not self._status.healthy and self._params.fail_closed_on_unhealthy:
            return True, f"event_filter unhealthy: {self._status.reason}"
        before = dt.timedelta(minutes=self._params.block_minutes_before_event)
        after = dt.timedelta(minutes=self._params.block_minutes_after_event)
        for ev in self._events:
            if ev.impact.lower() not in self._params.blocked_event_impact:
                continue
            if ev.currency.upper() not in self._params.blocked_currencies:
                continue
            window_start = ev.when_utc - before
            window_end = ev.when_utc + after
            if window_start <= ts_utc <= window_end:
                return True, f"in window of {ev.event_name} ({ev.currency} {ev.impact}) at {ev.when_utc.isoformat()}"
        return False, None


def _parse_row(row: dict[str, object]) -> CalendarEvent:
    date = str(row["date"])
    time_utc = str(row["time_utc"])
    when = dt.datetime.fromisoformat(f"{date}T{time_utc}").replace(tzinfo=dt.timezone.utc)
    return CalendarEvent(
        when_utc=when,
        event_name=str(row["event_name"]),
        currency=str(row["currency"]),
        impact=str(row["impact"]).lower(),
        source_url=str(row.get("source_url", "")),
    )


def write_skeleton_calendar(path: Path, events: Iterable[dict[str, object]] | None = None) -> None:
    """Helper: write a YAML calendar with a sample row (used in tests / setup)."""
    rows = list(events) if events is not None else [
        {
            "date": dt.date.today().isoformat(),
            "time_utc": "12:30",
            "event_name": "PLACEHOLDER — replace with real events",
            "currency": "USD",
            "impact": "high",
            "source_url": "https://www.forexfactory.com/calendar",
        }
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
