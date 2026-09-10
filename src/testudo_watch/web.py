from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.web_time import humanize_age, parse_db_utc

STALE_GRACE_SECONDS = 20


@dataclass(frozen=True)
class UnhealthyWatch:
    course_id: str
    term_id: str
    consecutive_failures: int
    last_error: str | None


@dataclass(frozen=True)
class StatusView:
    has_data: bool
    last_poll_age: str | None
    cycle_count: int | None
    stale: bool
    unhealthy: list[UnhealthyWatch]


@dataclass(frozen=True)
class SectionView:
    section_id: str
    open_seats: int
    total_seats: int
    waitlist: int
    is_open: bool
    updated_age: str


@dataclass(frozen=True)
class WatchView:
    course_id: str
    term_id: str
    section_label: str
    sections: list[SectionView]


@dataclass(frozen=True)
class NotificationView:
    sent_age: str
    course_id: str
    section_id: str
    open_seats: int
    channel: str
    status: str
    detail: str


def build_status_view(
    db: Database, config: AppConfig, *, now: datetime
) -> StatusView:
    health = db.get_watch_health()
    unhealthy = [
        UnhealthyWatch(c, t, h.consecutive_failures, h.last_error)
        for (c, t), h in sorted(health.items())
        if h.consecutive_failures > 0
    ]
    hb = db.get_heartbeat()
    if hb is None:
        return StatusView(
            has_data=False,
            last_poll_age=None,
            cycle_count=None,
            stale=True,
            unhealthy=unhealthy,
        )
    then = parse_db_utc(hb.updated_at)
    age = (now - then).total_seconds()
    stale = age > (2 * config.poll_interval_seconds + STALE_GRACE_SECONDS)
    return StatusView(
        has_data=True,
        last_poll_age=humanize_age(then, now),
        cycle_count=hb.cycle_count,
        stale=stale,
        unhealthy=unhealthy,
    )


def build_watches_view(db: Database, *, now: datetime) -> list[WatchView]:
    views: list[WatchView] = []
    for w in db.get_active_watches():
        rows = db.get_section_rows(w)
        if w.sections:
            wanted = set(w.sections)
            rows = [r for r in rows if r.section_id in wanted]
        sections = [
            SectionView(
                section_id=r.section_id,
                open_seats=r.open_seats,
                total_seats=r.total_seats,
                waitlist=r.waitlist,
                is_open=r.open_seats > 0,
                updated_age=humanize_age(parse_db_utc(r.updated_at), now),
            )
            for r in rows
        ]
        label = ", ".join(w.sections) if w.sections else "any section"
        views.append(WatchView(w.course_id, w.term_id, label, sections))
    return views


def build_notifications_view(
    db: Database, *, now: datetime, limit: int = 50
) -> list[NotificationView]:
    return [
        NotificationView(
            sent_age=humanize_age(parse_db_utc(n.sent_at), now),
            course_id=n.course_id,
            section_id=n.section_id,
            open_seats=n.open_seats,
            channel=n.channel,
            status=n.status,
            detail=n.detail,
        )
        for n in db.recent_notifications(limit)
    ]
