import sqlite3

import pytest

from testudo_watch.db import (
    Database,
    DatabaseError,
    Heartbeat,
    NotificationRow,
    SectionRow,
    WatchHealth,
)
from testudo_watch.models import SectionSnapshot, Watch


def make_db(tmp_path):
    return Database(tmp_path / "state.db")


def test_newer_schema_version_raises_database_error(tmp_path):
    path = tmp_path / "state.db"
    db = Database(path)
    db.connection.execute("PRAGMA user_version = 99")
    db.connection.commit()
    db.close()
    with pytest.raises(DatabaseError):
        Database(path)


def test_migrate_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    db.close()
    db2 = Database(tmp_path / "state.db")  # opening again re-runs migrate
    db2.close()


def test_sync_and_get_active_watches(tmp_path):
    db = make_db(tmp_path)
    db.sync_watches(
        [
            Watch("CMSC351", "202601", ("0101", "0201")),
            Watch("MATH240", "202601", ()),
        ]
    )
    assert db.get_active_watches() == [
        Watch("CMSC351", "202601", ("0101", "0201")),
        Watch("MATH240", "202601", ()),
    ]
    # Removing MATH240 from the file deactivates it, keeps CMSC351.
    db.sync_watches([Watch("CMSC351", "202601", ("0101", "0201"))])
    assert db.get_active_watches() == [Watch("CMSC351", "202601", ("0101", "0201"))]
    db.close()


def test_snapshot_roundtrip_keyed_by_section(tmp_path):
    db = make_db(tmp_path)
    w = Watch("CMSC351", "202601", ())
    db.upsert_snapshots(
        [
            SectionSnapshot("CMSC351", "202601", "0101", 200, 0, 3),
            SectionSnapshot("CMSC351", "202601", "0201", 90, 5, 0),
        ]
    )
    # Overwrite 0101 with a newer count.
    db.upsert_snapshots([SectionSnapshot("CMSC351", "202601", "0101", 200, 4, 3)])
    snaps = db.get_snapshots(w)
    assert snaps["0101"].open_seats == 4
    assert snaps["0201"].open_seats == 5
    assert set(snaps) == {"0101", "0201"}
    db.close()


def test_get_snapshots_scoped_to_watch_course_and_term(tmp_path):
    db = make_db(tmp_path)
    db.upsert_snapshots(
        [
            SectionSnapshot("CMSC351", "202601", "0101", 200, 1, 0),
            SectionSnapshot("MATH240", "202601", "0101", 30, 2, 0),
        ]
    )
    snaps = db.get_snapshots(Watch("CMSC351", "202601", ()))
    assert list(snaps) == ["0101"]
    assert snaps["0101"].course_id == "CMSC351"
    db.close()


def test_record_notification_appends_rows(tmp_path):
    db = make_db(tmp_path)
    snap = SectionSnapshot("CMSC351", "202601", "0101", 200, 6, 0)
    db.record_notification(snap, channel="console", status="sent")
    db.record_notification(snap, channel="sms", status="failed", detail="boom")
    rows = db.connection.execute(
        "SELECT course_id, section_id, open_seats, channel, status, detail "
        "FROM notifications ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("CMSC351", "0101", 6, "console", "sent", ""),
        ("CMSC351", "0101", 6, "sms", "failed", "boom"),
    ]
    db.close()


def test_fresh_db_is_schema_v2(tmp_path):
    db = Database(tmp_path / "s.db")
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    tables = {
        r[0]
        for r in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"watcher_heartbeat", "watch_health"} <= tables
    db.close()


def test_heartbeat_roundtrip_is_single_row(tmp_path):
    db = Database(tmp_path / "s.db")
    assert db.get_heartbeat() is None
    db.write_heartbeat(1)
    db.write_heartbeat(2)
    hb = db.get_heartbeat()
    assert isinstance(hb, Heartbeat) and hb.cycle_count == 2 and hb.updated_at
    assert (
        db.connection.execute("SELECT COUNT(*) FROM watcher_heartbeat").fetchone()[0]
        == 1
    )
    db.close()


def test_watch_health_failure_then_recovery(tmp_path):
    db = Database(tmp_path / "s.db")
    w = Watch("CMSC351", "202601", ())
    db.upsert_watch_health(w, ok=False, error="HTTP 503")
    db.upsert_watch_health(w, ok=False, error="HTTP 500")
    h = db.get_watch_health()[("CMSC351", "202601")]
    assert h.consecutive_failures == 2 and h.last_error == "HTTP 500"
    assert h.last_error_at and h.last_success_at is None

    db.upsert_watch_health(w, ok=True)
    h = db.get_watch_health()[("CMSC351", "202601")]
    assert h.consecutive_failures == 0 and h.last_success_at is not None
    assert h.last_error == "HTTP 500"  # retained for "recovered" context
    db.close()


def test_get_section_rows_has_updated_at_and_is_ordered(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_snapshots(
        [
            SectionSnapshot("CMSC351", "202601", "0201", 90, 0, 0),
            SectionSnapshot("CMSC351", "202601", "0101", 200, 5, 1),
        ]
    )
    rows = db.get_section_rows(Watch("CMSC351", "202601", ()))
    assert [r.section_id for r in rows] == ["0101", "0201"]
    assert isinstance(rows[0], SectionRow)
    assert rows[0].open_seats == 5 and rows[0].waitlist == 1 and rows[0].updated_at
    db.close()


def test_recent_notifications_newest_first_and_limited(tmp_path):
    db = Database(tmp_path / "s.db")
    for i in range(3):
        db.record_notification(
            SectionSnapshot("CMSC351", "202601", f"010{i}", i, 0, 0),
            channel="console",
            status="sent",
        )
    rows = db.recent_notifications(limit=2)
    assert [r.section_id for r in rows] == ["0102", "0101"]
    assert isinstance(rows[0], NotificationRow) and rows[0].channel == "console"
    db.close()


def test_read_only_open_of_missing_file_raises_operationalerror(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        Database(tmp_path / "nope.db", read_only=True)


def test_read_only_connection_rejects_writes(tmp_path):
    db = Database(tmp_path / "s.db")
    db.write_heartbeat(1)
    db.close()

    ro = Database(tmp_path / "s.db", read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.write_heartbeat(2)
    ro.close()


def test_read_only_open_does_not_write_user_version(tmp_path):
    handmade = tmp_path / "v0.db"
    con = sqlite3.connect(str(handmade))
    con.execute("CREATE TABLE placeholder (x INTEGER)")  # make it a real file
    con.commit()
    con.close()  # user_version is still 0 (CREATE TABLE does not bump it)
    ro = Database(handmade, read_only=True)
    assert ro.connection.execute("PRAGMA user_version").fetchone()[0] == 0
    ro.close()
