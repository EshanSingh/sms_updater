import pytest

import testudo_watch.engine as engine
from testudo_watch.db import Database
from testudo_watch.engine import format_opening, run
from testudo_watch.config import AppConfig
from testudo_watch.models import OpeningEvent, SectionSnapshot, Watch
from testudo_watch.notifier import NotifierError
from testudo_watch.scraper import ScrapeError


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    engine._stop = False
    yield
    engine._stop = False


def cfg():
    return AppConfig(
        poll_interval_seconds=30, notifier="console", db_path="x", log_dir="l"
    )


class FakeNotifier:
    def __init__(self, fail_on=()):
        self.sent = []
        self.fail_on = set(fail_on)

    def send(self, message):
        self.sent.append(message)
        for token in self.fail_on:
            if token in message:
                raise NotifierError("nope")


def snap(course, section, open_seats):
    return SectionSnapshot(course, "202601", section, 100, open_seats, 0)


def test_format_opening_names_course_section_and_seats():
    msg = format_opening(OpeningEvent(snap("CMSC351", "0101", 4)))
    assert "CMSC351" in msg and "0101" in msg and "4" in msg


def test_run_once_notifies_and_persists(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()

    def fake_fetch(session, course_id, term_id):
        return [snap(course_id, "0101", 6), snap(course_id, "0201", 0)]

    run(cfg(), db, notifier, session=None, once=True, fetch=fake_fetch,
        sleep=lambda s: None)

    assert len(notifier.sent) == 1 and "0101" in notifier.sent[0]
    assert db.get_snapshots(Watch("CMSC351", "202601", ()))["0101"].open_seats == 6
    rows = db.connection.execute("SELECT status FROM notifications").fetchall()
    assert [r["status"] for r in rows] == ["sent"]
    db.close()


def test_run_loop_runs_one_cycle_then_stops_on_sleep(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()

    def fake_fetch(session, course_id, term_id):
        return [snap("CMSC351", "0101", 6)]

    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            engine._stop = True

    try:
        result = run(
            cfg(), db, notifier, None, once=False,
            fetch=fake_fetch, sleep=fake_sleep,
        )
        assert result is None
        assert len(notifier.sent) == 1 and "0101" in notifier.sent[0]
        assert db.get_snapshots(Watch("CMSC351", "202601", ()))["0101"].open_seats == 6
    finally:
        engine._stop = False
        db.close()


def test_run_once_second_pass_is_silent_when_seats_unchanged(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()
    fetch = lambda s, c, t: [snap(c, "0101", 6)]
    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    assert len(notifier.sent) == 1
    db.close()


def test_failed_send_leaves_snapshot_stale_so_it_retries(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier(fail_on=["0101"])
    fetch = lambda s, c, t: [snap(c, "0101", 6)]

    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    # snapshot NOT written (still no prior), so a second pass notifies again
    assert db.get_snapshots(Watch("CMSC351", "202601", ())) == {}
    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    assert len(notifier.sent) == 2
    rows = db.connection.execute("SELECT status FROM notifications").fetchall()
    assert [r["status"] for r in rows] == ["failed", "failed"]
    db.close()


def test_scrape_error_is_swallowed_and_counted(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()
    counts = {}

    def boom(session, course_id, term_id):
        raise ScrapeError("testudo down")

    run(cfg(), db, notifier, None, once=True, fetch=boom, sleep=lambda s: None,
        failure_counts=counts)
    assert counts["CMSC351/202601"] == 1
    assert notifier.sent == []
    db.close()


def test_health_alert_fires_once_at_threshold(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()
    counts = {}

    def boom(session, course_id, term_id):
        raise ScrapeError("down")

    for _ in range(7):
        run(cfg(), db, notifier, None, once=True, fetch=boom,
            sleep=lambda s: None, failure_counts=counts)

    health = [m for m in notifier.sent if "consecutive" in m]
    assert len(health) == 1
    db.close()
