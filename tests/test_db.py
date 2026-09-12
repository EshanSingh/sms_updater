import sqlite3
from pathlib import Path

import pytest

from testudo_watch.db import (
    Database,
    DatabaseError,
    Heartbeat,
    NotificationPage,
    NotificationRow,
    SectionRow,
    WatchHealth,
    WatchRow,
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


def test_creates_missing_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.db"
    db = Database(path)
    db.close()
    assert path.exists()


def test_parent_directory_creation_failure_raises_friendly_error(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "state.db"

    def boom(self, parents=False, exist_ok=False):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(DatabaseError, match="disk full"):
        Database(path)


def test_migrate_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    db.close()
    db2 = Database(tmp_path / "state.db")  # opening again re-runs migrate
    db2.close()


def test_migrate_is_a_true_no_op_when_already_current(tmp_path):
    path = tmp_path / "s.db"
    Database(path).close()  # first open: full migration
    reader = sqlite3.connect(str(path))
    before = reader.execute("PRAGMA data_version").fetchone()[0]
    Database(path).close()  # second open: must NOT write
    after = reader.execute("PRAGMA data_version").fetchone()[0]
    assert after == before
    reader.close()


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


def test_fresh_db_is_schema_v3(tmp_path):
    db = Database(tmp_path / "s.db")
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 3
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


def _v2_db_without_source(path):
    """A hand-built pre-v3 database: watches has no `source` column, user_version=2."""
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE watches (course_id TEXT NOT NULL, term_id TEXT NOT NULL, "
        "sections_csv TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, "
        "PRIMARY KEY (course_id, term_id));"
    )
    con.execute(
        "INSERT INTO watches (course_id, term_id, sections_csv, active) "
        "VALUES ('CMSC351', '202601', '0101', 1)"
    )
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()


def test_migrate_v2_to_v3_adds_source_column(tmp_path):
    path = tmp_path / "v2.db"
    _v2_db_without_source(path)
    db = Database(path)
    cols = {r[1] for r in db.connection.execute("PRAGMA table_info(watches)")}
    assert "source" in cols
    row = db.connection.execute(
        "SELECT source FROM watches WHERE course_id = 'CMSC351'"
    ).fetchone()
    assert row["source"] == "file"
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    db.close()


def test_fresh_db_has_source_column_and_v3(tmp_path):
    db = Database(tmp_path / "fresh.db")
    cols = {r[1] for r in db.connection.execute("PRAGMA table_info(watches)")}
    assert "source" in cols
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    db.close()


def test_migrate_reopen_is_idempotent(tmp_path):
    path = tmp_path / "v2.db"
    _v2_db_without_source(path)
    Database(path).close()
    Database(path).close()  # second open must not error on a duplicate ALTER


def test_write_mode_enables_wal_and_busy_timeout(tmp_path):
    db = Database(tmp_path / "s.db")
    assert db.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    db.close()


def test_read_only_open_of_wal_db_still_works(tmp_path):
    path = tmp_path / "s.db"
    w = Database(path)
    w.write_heartbeat(1)
    w.close()
    ro = Database(path, read_only=True)
    assert ro.get_heartbeat().cycle_count == 1
    assert ro.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    ro.close()


def test_sync_watches_ignores_ui_rows(tmp_path):
    db = Database(tmp_path / "s.db")
    db.connection.execute(
        "INSERT INTO watches (course_id, term_id, sections_csv, active, source) "
        "VALUES ('MATH240', '202601', '0111', 1, 'ui')"
    )
    db.connection.commit()
    # a sync that does NOT mention MATH240 must leave the ui row active & unchanged
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    rows = {
        (r["course_id"], r["term_id"]): (r["active"], r["source"], r["sections_csv"])
        for r in db.connection.execute(
            "SELECT course_id, term_id, active, source, sections_csv FROM watches"
        )
    }
    assert rows[("MATH240", "202601")] == (1, "ui", "0111")
    assert rows[("CMSC351", "202601")] == (1, "file", "0101")
    db.close()


def test_sync_watches_still_deactivates_absent_file_rows(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ()), Watch("MATH240", "202601", ())])
    db.sync_watches([Watch("CMSC351", "202601", ())])  # MATH240 dropped from file
    active = {
        (r["course_id"], r["term_id"])
        for r in db.connection.execute(
            "SELECT course_id, term_id FROM watches WHERE active = 1"
        )
    }
    assert active == {("CMSC351", "202601")}
    db.close()


def test_sync_watches_does_not_reactivate_ui_disabled_row(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.connection.execute(
        "UPDATE watches SET active = 0, source = 'ui' WHERE course_id = 'CMSC351'"
    )
    db.connection.commit()
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])  # still in the file
    row = db.connection.execute(
        "SELECT active, source FROM watches WHERE course_id = 'CMSC351'"
    ).fetchone()
    assert (row["active"], row["source"]) == (0, "ui")
    db.close()


def test_sync_watches_matches_existing_row_case_insensitively(tmp_path):
    db = Database(tmp_path / "s.db")
    # a legacy row written before course_id normalization was added
    db.connection.execute(
        "INSERT INTO watches (course_id, term_id, sections_csv, active, source) "
        "VALUES ('cmsc351', '202601', '0101', 1, 'file')"
    )
    db.upsert_snapshots(
        [SectionSnapshot("cmsc351", "202601", "0101", 200, 5, 0)]
    )
    db.connection.commit()

    # config.load_config now normalizes course_id to upper-case before this call
    db.sync_watches([Watch("CMSC351", "202601", ("0101", "0201"))])

    rows = db.connection.execute(
        "SELECT course_id, term_id, active, source, sections_csv FROM watches"
    ).fetchall()
    assert len(rows) == 1  # no duplicate row created
    row = rows[0]
    assert row["course_id"] == "cmsc351"  # original casing preserved, not renamed
    assert (row["active"], row["source"], row["sections_csv"]) == (1, "file", "0101,0201")

    # history under the old casing is still reachable — nothing orphaned
    snaps = db.get_snapshots(Watch("cmsc351", "202601", ()))
    assert snaps["0101"].open_seats == 5
    db.close()


def test_add_or_replace_ui_watch_sets_ui_source(tmp_path):
    db = Database(tmp_path / "s.db")
    db.add_or_replace_ui_watch("CMSC351", "202601", ("0101", "0201"))
    r = db.watch_row("CMSC351", "202601")
    assert r == WatchRow("CMSC351", "202601", ("0101", "0201"), True, "ui")
    # re-add replaces the section list, stays ui/active
    db.add_or_replace_ui_watch("CMSC351", "202601", ("0301",))
    assert db.watch_row("CMSC351", "202601").sections == ("0301",)
    db.close()


def test_add_or_replace_reactivates_a_disabled_row(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.set_watch_active("CMSC351", "202601", False)
    db.add_or_replace_ui_watch("CMSC351", "202601", ("0101", "0201"))
    r = db.watch_row("CMSC351", "202601")
    assert (r.active, r.source, r.sections) == (True, "ui", ("0101", "0201"))
    db.close()


def test_set_watch_sections_flips_source_to_ui(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.set_watch_sections("CMSC351", "202601", ("0101", "0202"))
    r = db.watch_row("CMSC351", "202601")
    assert r.sections == ("0101", "0202") and r.source == "ui"
    db.close()


def test_set_watch_active_flips_source_to_ui(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    db.set_watch_active("CMSC351", "202601", False)
    r = db.watch_row("CMSC351", "202601")
    assert r.active is False and r.source == "ui"
    db.set_watch_active("CMSC351", "202601", True)
    assert db.watch_row("CMSC351", "202601").active is True
    db.close()


def test_delete_watch_removes_row_and_related(tmp_path):
    db = Database(tmp_path / "s.db")
    db.add_or_replace_ui_watch("CMSC351", "202601", ())
    db.upsert_watch_health(Watch("CMSC351", "202601", ()), ok=False, error="x")
    db.upsert_snapshots(
        [SectionSnapshot("CMSC351", "202601", "0101", 10, 1, 0)]
    )
    db.delete_watch("CMSC351", "202601")
    assert db.watch_row("CMSC351", "202601") is None
    assert db.get_watch_health() == {}
    assert (
        db.connection.execute("SELECT COUNT(*) FROM section_snapshots").fetchone()[0]
        == 0
    )
    db.close()


def test_get_all_watches_includes_inactive_in_rowid_order(tmp_path):
    db = Database(tmp_path / "s.db")
    db.add_or_replace_ui_watch("CMSC351", "202601", ("0101",))
    db.add_or_replace_ui_watch("MATH240", "202601", ())
    db.set_watch_active("CMSC351", "202601", False)
    rows = db.get_all_watches()
    assert [(r.course_id, r.active) for r in rows] == [
        ("CMSC351", False),
        ("MATH240", True),
    ]
    db.close()


def test_watch_row_none_when_absent(tmp_path):
    db = Database(tmp_path / "s.db")
    assert db.watch_row("NONE", "000000") is None
    db.close()


def _seed_notifications(db, entries):
    """entries: list of (course_id, section_id, open_seats, sent_at)."""
    for course_id, section_id, open_seats, sent_at in entries:
        db.connection.execute(
            "INSERT INTO notifications "
            "(course_id, term_id, section_id, open_seats, sent_at, channel, status, detail) "
            "VALUES (?, '202601', ?, ?, ?, 'console', 'sent', '')",
            (course_id, section_id, open_seats, sent_at),
        )
    db.connection.commit()


def test_query_notifications_no_filters_returns_all_newest_first(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 2, "2026-09-02 10:00:00"),
        ],
    )
    page = db.query_notifications()
    assert isinstance(page, NotificationPage)
    assert page.total == 2
    assert [r.course_id for r in page.rows] == ["MATH240", "CMSC351"]
    db.close()


def test_query_notifications_filters_by_course(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 2, "2026-09-02 10:00:00"),
        ],
    )
    page = db.query_notifications(course_id="CMSC351")
    assert page.total == 1
    assert [r.course_id for r in page.rows] == ["CMSC351"]
    db.close()


def test_query_notifications_filters_by_date_range_inclusive(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("CMSC351", "0101", 1, "2026-09-01 00:00:00"),  # exactly since boundary
            ("CMSC351", "0201", 1, "2026-09-05 12:00:00"),  # inside range
            ("CMSC351", "0301", 1, "2026-09-10 23:59:59"),  # exactly until boundary
            ("CMSC351", "0401", 1, "2026-08-31 23:59:59"),  # just before since
            ("CMSC351", "0501", 1, "2026-09-11 00:00:00"),  # just after until
        ],
    )
    page = db.query_notifications(since="2026-09-01", until="2026-09-10")
    assert page.total == 3
    assert {r.section_id for r in page.rows} == {"0101", "0201", "0301"}
    db.close()


def test_query_notifications_paginates(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("CMSC351", f"0{i:03d}", 1, f"2026-09-01 10:{i:02d}:00")
            for i in range(5)
        ],
    )
    page1 = db.query_notifications(page=1, page_size=2)
    page2 = db.query_notifications(page=2, page_size=2)
    assert page1.total == 5 and page2.total == 5
    assert [r.section_id for r in page1.rows] == ["0004", "0003"]
    assert [r.section_id for r in page2.rows] == ["0002", "0001"]
    db.close()


def test_notification_course_ids_distinct_sorted_includes_deleted_watch(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("MATH240", "0111", 1, "2026-09-01 10:00:00"),
            ("CMSC351", "0101", 1, "2026-09-02 10:00:00"),
            ("CMSC351", "0201", 1, "2026-09-03 10:00:00"),
        ],
    )
    # delete_watch removes the watches/watch_health/section_snapshots rows for
    # MATH240 but intentionally leaves its notifications history in place.
    db.add_or_replace_ui_watch("MATH240", "202601", ())
    db.delete_watch("MATH240", "202601")
    assert db.notification_course_ids() == ["CMSC351", "MATH240"]
    db.close()
