from datetime import datetime, timezone

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot, Watch
from testudo_watch.web import (
    build_notifications_view,
    build_status_view,
    build_watches_view,
)

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path=str(db_path),
        log_dir="logs",
    )


def seed(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.connection.execute(
        "INSERT INTO section_snapshots "
        "(course_id, term_id, section_id, total_seats, open_seats, waitlist, updated_at) "
        "VALUES ('CMSC351','202601','0101',200,4,0,'2026-09-10 11:59:50')"
    )
    db.connection.execute(
        "INSERT INTO section_snapshots "
        "(course_id, term_id, section_id, total_seats, open_seats, waitlist, updated_at) "
        "VALUES ('CMSC351','202601','0201',200,0,0,'2026-09-10 11:59:50')"
    )
    db.connection.commit()
    return db


def _set_heartbeat(db, updated_at, cycle_count):
    db.connection.execute(
        "INSERT INTO watcher_heartbeat (id, updated_at, cycle_count) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, "
        "cycle_count=excluded.cycle_count",
        (updated_at, cycle_count),
    )
    db.connection.commit()


def test_status_view_fresh_is_not_stale(tmp_path):
    db = seed(tmp_path)
    _set_heartbeat(db, "2026-09-10 11:59:40", 7)  # 20s before NOW
    sv = build_status_view(db, cfg(tmp_path / "s.db"), now=NOW)
    assert sv.has_data and sv.cycle_count == 7 and sv.stale is False
    assert sv.unhealthy == []
    assert sv.last_poll_age == "20s ago"
    db.close()


def test_status_view_no_heartbeat_is_stale_without_data(tmp_path):
    db = seed(tmp_path)
    sv = build_status_view(db, cfg(tmp_path / "s.db"), now=NOW)
    assert sv.has_data is False and sv.stale is True and sv.cycle_count is None
    db.close()


def test_status_view_old_heartbeat_is_stale(tmp_path):
    db = seed(tmp_path)
    _set_heartbeat(db, "2026-09-10 11:00:00", 3)  # 1h before NOW, >> 2*30+20
    sv = build_status_view(db, cfg(tmp_path / "s.db"), now=NOW)
    assert sv.stale is True and sv.cycle_count == 3
    db.close()


def test_status_view_lists_unhealthy_watches(tmp_path):
    db = seed(tmp_path)
    _set_heartbeat(db, "2026-09-10 11:59:40", 7)
    db.upsert_watch_health(Watch("CMSC351", "202601", ()), ok=False, error="HTTP 503")
    sv = build_status_view(db, cfg(tmp_path / "s.db"), now=NOW)
    assert [(u.course_id, u.consecutive_failures) for u in sv.unhealthy] == [
        ("CMSC351", 1)
    ]
    assert sv.unhealthy[0].last_error == "HTTP 503"
    db.close()


def test_status_view_omits_unhealthy_for_inactive_watch(tmp_path):
    db = seed(tmp_path)
    _set_heartbeat(db, "2026-09-10 11:59:40", 7)
    db.upsert_watch_health(
        Watch("CMSC351", "202601", ()), ok=False, error="HTTP 503"
    )
    # Watch removed from watches.toml -> deactivated; its stale failure must
    # not linger as a permanent banner.
    db.sync_watches([])
    sv = build_status_view(db, cfg(tmp_path / "s.db"), now=NOW)
    assert sv.unhealthy == []
    db.close()


def test_watches_view_filters_to_watched_sections(tmp_path):
    db = seed(tmp_path)
    views = build_watches_view(db, now=NOW)
    assert len(views) == 1
    v = views[0]
    assert v.course_id == "CMSC351" and v.section_label == "0101"
    assert [s.section_id for s in v.sections] == ["0101"]
    assert v.sections[0].is_open is True and v.sections[0].updated_age == "10s ago"
    db.close()


def test_watches_view_any_section_shows_all(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("MATH240", "202601", ())])
    db.connection.execute(
        "INSERT INTO section_snapshots "
        "(course_id, term_id, section_id, total_seats, open_seats, waitlist, updated_at) "
        "VALUES ('MATH240','202601','0111',29,6,0,'2026-09-10 11:59:00')"
    )
    db.connection.commit()
    v = build_watches_view(db, now=NOW)[0]
    assert v.section_label == "any section"
    assert [s.section_id for s in v.sections] == ["0111"]
    db.close()


def test_notifications_view_shape_and_order(tmp_path):
    db = seed(tmp_path)
    for i in range(2):
        db.record_notification(
            SectionSnapshot("CMSC351", "202601", f"010{i}", i, 0, 0),
            channel="console",
            status="sent" if i == 0 else "failed",
        )
    views = build_notifications_view(db, now=datetime.now(timezone.utc))
    assert [v.section_id for v in views] == ["0101", "0100"]  # newest first
    assert views[0].status == "failed" and views[1].status == "sent"
    assert views[0].sent_age.endswith("ago")
    db.close()
