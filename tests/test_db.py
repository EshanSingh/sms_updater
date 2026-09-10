import pytest

from testudo_watch.db import Database, DatabaseError
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
