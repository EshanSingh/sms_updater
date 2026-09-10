from __future__ import annotations

import logging
import random
import signal
import threading
import time
from collections.abc import Callable

import requests

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.diff import detect_openings
from testudo_watch.models import OpeningEvent, SectionSnapshot
from testudo_watch.notifier import Notifier, NotifierError
from testudo_watch.scraper import ScrapeError, fetch_sections

_log = logging.getLogger("testudo_watch.engine")

FAILURE_ALERT_THRESHOLD = 5
POLITE_DELAY_SECONDS = 3
JITTER_MAX_SECONDS = 5

_stop = False


def _request_stop(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    _log.info("stop signal received; finishing current cycle")


def format_opening(event: OpeningEvent) -> str:
    s = event.snapshot
    return (
        f"{s.course_id} section {s.section_id} ({s.term_id}) has "
        f"{s.open_seats} open seat(s) [total {s.total_seats}]"
    )


def _process_watch(config, db, notifier, session, watch, fetch, failure_counts) -> None:
    key = f"{watch.course_id}/{watch.term_id}"
    try:
        current = fetch(session, watch.course_id, watch.term_id)
    except (ScrapeError, requests.RequestException) as exc:
        _log.error("scrape failed for %s", key, exc_info=True)
        failure_counts[key] = failure_counts.get(key, 0) + 1
        db.upsert_watch_health(watch, ok=False, error=str(exc))
        if failure_counts[key] == FAILURE_ALERT_THRESHOLD:
            msg = (
                f"testudo-watch: {watch.course_id} has failed "
                f"{FAILURE_ALERT_THRESHOLD} consecutive checks"
            )
            try:
                notifier.send(msg)
            except NotifierError as send_exc:
                _log.error("health alert send failed", exc_info=True)
                db.record_notification(
                    SectionSnapshot(watch.course_id, watch.term_id, "-", 0, 0, 0),
                    channel=config.notifier,
                    status="health-failed",
                    detail=str(send_exc),
                )
            else:
                db.record_notification(
                    SectionSnapshot(watch.course_id, watch.term_id, "-", 0, 0, 0),
                    channel=config.notifier,
                    status="health",
                )
        return

    failure_counts[key] = 0
    db.upsert_watch_health(watch, ok=True)
    previous = db.get_snapshots(watch)
    events = detect_openings(previous, current, watch)

    failed_sections: set[str] = set()
    for event in events:
        message = format_opening(event)
        _log.warning("opening detected: %s", message)
        try:
            notifier.send(message)
        except NotifierError as exc:
            _log.error("notify failed for %s", message, exc_info=True)
            failed_sections.add(event.snapshot.section_id)
            db.record_notification(
                event.snapshot,
                channel=config.notifier,
                status="failed",
                detail=str(exc),
            )
        else:
            db.record_notification(
                event.snapshot, channel=config.notifier, status="sent"
            )

    to_persist = [s for s in current if s.section_id not in failed_sections]
    db.upsert_snapshots(to_persist)
    _log.info(
        "%s: %s",
        key,
        ", ".join(f"{s.section_id}={s.open_seats}" for s in current) or "no sections",
    )


def run(
    config: AppConfig,
    db: Database,
    notifier: Notifier,
    session: requests.Session | None,
    *,
    once: bool = False,
    fetch: Callable = fetch_sections,
    sleep: Callable[[float], None] = time.sleep,
    failure_counts: dict[str, int] | None = None,
) -> None:
    global _stop
    _stop = False
    if failure_counts is None:
        failure_counts = {}

    previous_handlers = {}
    if not once and threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _request_stop)

    try:
        cycle_count = 0
        while True:
            cycle_count += 1
            for index, watch in enumerate(db.get_active_watches()):
                if _stop:
                    return
                if index > 0:
                    # polite delay BETWEEN watches, in both once and loop modes
                    sleep(POLITE_DELAY_SECONDS)
                _process_watch(
                    config, db, notifier, session, watch, fetch, failure_counts
                )
            db.write_heartbeat(cycle_count)
            if once or _stop:
                return
            sleep(config.poll_interval_seconds + random.uniform(0, JITTER_MAX_SECONDS))
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
