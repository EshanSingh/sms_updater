from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from testudo_watch.config import AppConfig
from testudo_watch.db import Database, DatabaseError as SchemaError
from testudo_watch.scraper import ScrapeError, build_session, fetch_sections
from testudo_watch.web_time import humanize_age, parse_db_utc

_log = logging.getLogger("testudo_watch.web")

STALE_GRACE_SECONDS = 20

_COURSE_RE = re.compile(r"^[A-Z]{4}\d{3}[A-Z]?$")
_TERM_RE = re.compile(r"^\d{6}$")


class ProbeError(Exception):
    """A watch could not be validated against Testudo."""


def _parse_sections(raw: str) -> tuple[str, ...]:
    return tuple(s for s in re.split(r"[,\s]+", raw.strip()) if s)


def _probe(course_id: str, term_id: str) -> list[str]:
    session = build_session()
    try:
        snaps = fetch_sections(session, course_id, term_id)
    except (ScrapeError, requests.RequestException) as exc:
        raise ProbeError(
            f"Testudo returned no sections for {course_id} in {term_id} "
            f"— check the course id and term ({exc})"
        ) from exc
    finally:
        session.close()
    return [s.section_id for s in snaps]


def _validate_and_probe(course_id: str, term_id: str, sections: tuple[str, ...]):
    """Returns normalized (course_id, sections). Raises ProbeError on any failure."""
    course_id = course_id.strip().upper()
    term_id = term_id.strip()
    if not _COURSE_RE.match(course_id):
        raise ProbeError(f"course id {course_id!r} looks wrong (expected e.g. CMSC351)")
    if not _TERM_RE.match(term_id):
        raise ProbeError(f"term id {term_id!r} must be 6 digits (e.g. 202601)")
    available = _probe(course_id, term_id)
    missing = [s for s in sections if s not in available]
    if missing:
        raise ProbeError(
            f"section {', '.join(missing)} not found. Available: {', '.join(sorted(available))}"
        )
    return course_id, term_id, sections


def _is_cross_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return False  # no Origin header (e.g. a same-origin browser form post) — allow
    from urllib.parse import urlsplit

    o = urlsplit(origin)
    o_port = o.port or (443 if o.scheme == "https" else 80)
    r_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return (o.hostname, o_port) != (request.url.hostname, r_port)


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
    active = {(w.course_id, w.term_id) for w in db.get_active_watches()}
    unhealthy = [
        UnhealthyWatch(c, t, h.consecutive_failures, h.last_error)
        for (c, t), h in sorted(health.items())
        if (c, t) in active and h.consecutive_failures > 0
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


@dataclass(frozen=True)
class ManageRow:
    course_id: str
    term_id: str
    section_label: str
    source: str
    in_file: bool


@dataclass(frozen=True)
class ManageView:
    active: list[ManageRow]
    disabled: list[ManageRow]


def build_manage_view(db: Database, file_watches) -> ManageView:
    in_file = {(w.course_id, w.term_id) for w in file_watches}
    active: list[ManageRow] = []
    disabled: list[ManageRow] = []
    for w in db.get_all_watches():
        row = ManageRow(
            course_id=w.course_id,
            term_id=w.term_id,
            section_label=", ".join(w.sections),
            source=w.source,
            in_file=(w.course_id, w.term_id) in in_file,
        )
        (active if w.active else disabled).append(row)
    return ManageView(active=active, disabled=disabled)


_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@contextmanager
def _open_db(config: AppConfig):
    try:
        db = Database(config.db_path)
    except (sqlite3.DatabaseError, SchemaError) as exc:
        _log.warning("could not open %s: %s", config.db_path, exc)
        yield None
        return
    try:
        yield db
    finally:
        db.close()


def create_app(config: AppConfig, file_watches) -> FastAPI:
    file_watches = list(file_watches)
    app = FastAPI(title="testudo-watch")

    def _load_views() -> dict | None:
        now = datetime.now(timezone.utc)
        try:
            db = Database(config.db_path)
        except (sqlite3.DatabaseError, SchemaError) as exc:
            _log.warning("dashboard could not open %s: %s", config.db_path, exc)
            return None
        try:
            return {
                "status": build_status_view(db, config, now=now),
                "watches": build_watches_view(db, now=now),
                "notifications": build_notifications_view(db, now=now),
            }
        except sqlite3.DatabaseError as exc:
            _log.warning("dashboard could not read %s: %s", config.db_path, exc)
            return None
        finally:
            db.close()

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        views = _load_views()
        if views is None:
            return _TEMPLATES.TemplateResponse(request, "no_data.html", {})
        return _TEMPLATES.TemplateResponse(request, "dashboard.html", views)

    @app.get("/fragments/status", response_class=HTMLResponse)
    def fragment_status(request: Request):
        views = _load_views()
        if views is None:
            return _TEMPLATES.TemplateResponse(request, "_no_data.html", {})
        return _TEMPLATES.TemplateResponse(
            request, "_status.html", {"status": views["status"]}
        )

    @app.get("/fragments/watches", response_class=HTMLResponse)
    def fragment_watches(request: Request):
        views = _load_views()
        if views is None:
            return _TEMPLATES.TemplateResponse(request, "_no_data.html", {})
        return _TEMPLATES.TemplateResponse(
            request, "_watches.html", {"watches": views["watches"]}
        )

    @app.get("/fragments/notifications", response_class=HTMLResponse)
    def fragment_notifications(request: Request):
        views = _load_views()
        if views is None:
            return _TEMPLATES.TemplateResponse(request, "_no_data.html", {})
        return _TEMPLATES.TemplateResponse(
            request, "_notifications.html", {"notifications": views["notifications"]}
        )

    @app.get("/watches", response_class=HTMLResponse)
    def watches_page(request: Request):
        with _open_db(config) as db:
            if db is None:
                return _TEMPLATES.TemplateResponse(request, "no_data.html", {})
            view = build_manage_view(db, file_watches)
        return _TEMPLATES.TemplateResponse(
            request, "manage.html", {"view": view, "error": None, "notice": None}
        )

    def _manage_response(request: Request, db, *, error=None, notice=None, status=200):
        view = build_manage_view(db, file_watches)
        return _TEMPLATES.TemplateResponse(
            request, "_manage.html",
            {"view": view, "error": error, "notice": notice},
            status_code=status,
        )

    @app.post("/watches", response_class=HTMLResponse)
    async def add_watch(request: Request):
        if _is_cross_origin(request):
            return HTMLResponse(
                '<p class="banner">Cross-origin request rejected.</p>', status_code=403
            )
        form = await request.form()
        with _open_db(config) as db:
            if db is None:
                return HTMLResponse(
                    '<p class="banner">Database is busy or unreadable — try again.</p>',
                    status_code=503,
                )
            try:
                course_id, term_id, sections = _validate_and_probe(
                    form.get("course_id", ""),
                    form.get("term_id", ""),
                    _parse_sections(form.get("sections", "")),
                )
            except ProbeError as exc:
                return _manage_response(request, db, error=str(exc), status=422)
            try:
                db.add_or_replace_ui_watch(course_id, term_id, sections)
            except sqlite3.OperationalError as exc:
                _log.warning("write failed for %s %s: %s", course_id, term_id, exc)
                return HTMLResponse(
                    '<p class="banner">Database is busy — try again.</p>', status_code=503
                )
            return _manage_response(request, db, notice=f"Watching {course_id} {term_id}.")

    @app.post("/watches/{course_id}/{term_id}/sections", response_class=HTMLResponse)
    async def edit_sections(request: Request, course_id: str, term_id: str):
        if _is_cross_origin(request):
            return HTMLResponse(
                '<p class="banner">Cross-origin request rejected.</p>', status_code=403
            )
        form = await request.form()
        with _open_db(config) as db:
            if db is None:
                return HTMLResponse(
                    '<p class="banner">Database is busy or unreadable — try again.</p>',
                    status_code=503,
                )
            row = db.watch_row(course_id, term_id)
            if row is None:
                return _manage_response(request, db, error="No such watch.", status=404)
            try:
                _, _, sections = _validate_and_probe(
                    row.course_id, row.term_id, _parse_sections(form.get("sections", ""))
                )
            except ProbeError as exc:
                return _manage_response(request, db, error=str(exc), status=422)
            try:
                db.set_watch_sections(row.course_id, row.term_id, sections)
            except sqlite3.OperationalError as exc:
                _log.warning(
                    "write failed for %s %s: %s", row.course_id, row.term_id, exc
                )
                return HTMLResponse(
                    '<p class="banner">Database is busy — try again.</p>', status_code=503
                )
            return _manage_response(request, db, notice="Sections updated.")

    @app.post("/watches/{course_id}/{term_id}/active", response_class=HTMLResponse)
    async def toggle_active(request: Request, course_id: str, term_id: str):
        if _is_cross_origin(request):
            return HTMLResponse(
                '<p class="banner">Cross-origin request rejected.</p>', status_code=403
            )
        form = await request.form()
        with _open_db(config) as db:
            if db is None:
                return HTMLResponse(
                    '<p class="banner">Database is busy or unreadable — try again.</p>',
                    status_code=503,
                )
            row = db.watch_row(course_id, term_id)
            if row is None:
                return _manage_response(request, db, error="No such watch.", status=404)
            try:
                db.set_watch_active(row.course_id, row.term_id, form.get("active") == "1")
            except sqlite3.OperationalError as exc:
                _log.warning(
                    "write failed for %s %s: %s", row.course_id, row.term_id, exc
                )
                return HTMLResponse(
                    '<p class="banner">Database is busy — try again.</p>', status_code=503
                )
            return _manage_response(request, db)

    @app.post("/watches/{course_id}/{term_id}/delete", response_class=HTMLResponse)
    async def delete_watch_route(request: Request, course_id: str, term_id: str):
        if _is_cross_origin(request):
            return HTMLResponse(
                '<p class="banner">Cross-origin request rejected.</p>', status_code=403
            )
        with _open_db(config) as db:
            if db is None:
                return HTMLResponse(
                    '<p class="banner">Database is busy or unreadable — try again.</p>',
                    status_code=503,
                )
            row = db.watch_row(course_id, term_id)
            if row is None:
                return _manage_response(request, db, error="No such watch.", status=404)
            if (row.course_id, row.term_id) in {
                (w.course_id, w.term_id) for w in file_watches
            }:
                try:
                    db.set_watch_active(row.course_id, row.term_id, False)
                except sqlite3.OperationalError as exc:
                    _log.warning(
                        "write failed for %s %s: %s", row.course_id, row.term_id, exc
                    )
                    return HTMLResponse(
                        '<p class="banner">Database is busy — try again.</p>',
                        status_code=503,
                    )
                return _manage_response(
                    request, db,
                    notice=f"{row.course_id} is still in watches.toml — disabled, not "
                    f"deleted. Remove it from the file to delete it permanently.",
                )
            try:
                db.delete_watch(row.course_id, row.term_id)
            except sqlite3.OperationalError as exc:
                _log.warning(
                    "write failed for %s %s: %s", row.course_id, row.term_id, exc
                )
                return HTMLResponse(
                    '<p class="banner">Database is busy — try again.</p>', status_code=503
                )
            return _manage_response(
                request, db, notice=f"Deleted {row.course_id} {row.term_id}."
            )

    return app
