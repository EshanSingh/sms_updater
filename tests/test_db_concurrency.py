"""Proves WAL + busy_timeout actually prevents run/serve lock contention.

Two connections writing to the same SQLite file concurrently is exactly what
`testudo-watch run` and `testudo-watch serve` do in production. Everywhere
else in the test suite exercises one Database connection at a time, so
nothing before this file ever put real overlapping writers against the same
file — the WAL + busy_timeout configuration was asserted (see
test_write_mode_enables_wal_and_busy_timeout in test_db.py) but never
actually stress-tested.
"""

from __future__ import annotations

import sqlite3
import threading

from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot, Watch

THREADS = 6
ITERATIONS = 20


def _snapshot(course_id: str, open_seats: int) -> SectionSnapshot:
    return SectionSnapshot(course_id, "202601", "0101", 100, open_seats, 0)


def test_concurrent_writers_survive_with_wal_and_busy_timeout(tmp_path):
    path = tmp_path / "s.db"
    Database(path).close()  # create + migrate once, before any thread opens it

    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def writer(thread_id: int) -> None:
        course_id = f"CRS{thread_id}"
        watch = Watch(course_id, "202601", ())
        try:
            db = Database(path)
            try:
                for i in range(ITERATIONS):
                    db.upsert_snapshots([_snapshot(course_id, i)])
                    db.upsert_watch_health(watch, ok=True)
                    db.write_heartbeat(i)
                    db.add_or_replace_ui_watch(course_id, "202601", (f"0{i % 9}",))
                    db.record_notification(
                        _snapshot(course_id, i), channel="console", status="sent"
                    )
            finally:
                db.close()
        except BaseException as exc:  # noqa: BLE001 - captured for the main thread
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []

    db = Database(path)
    assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.get_heartbeat() is not None
    counts = {
        table: db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("watches", "watch_health", "notifications")
    }
    assert counts == {
        "watches": THREADS,  # one distinct course_id per thread
        "watch_health": THREADS,
        "notifications": THREADS * ITERATIONS,  # pure inserts: none can be lost silently
    }
    db.close()


def test_same_workload_hits_database_locked_without_busy_timeout(tmp_path):
    # Negative control: proves the workload above genuinely creates lock
    # contention, so the positive test passing means the busy_timeout config
    # is doing real work — not that contention just never happened to occur.
    path = tmp_path / "s.db"
    setup = sqlite3.connect(str(path))
    setup.execute("PRAGMA journal_mode = WAL")
    setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, thread_id INTEGER, i INTEGER)")
    setup.commit()
    setup.close()

    lock_errors = 0
    lock_errors_lock = threading.Lock()
    other_errors: list[BaseException] = []

    def writer(thread_id: int) -> None:
        nonlocal lock_errors
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA busy_timeout = 0")  # no retry on contention
        try:
            for i in range(ITERATIONS * 5):  # more write pressure, no per-call overhead
                try:
                    with con:
                        con.execute(
                            "INSERT INTO t (thread_id, i) VALUES (?, ?)", (thread_id, i)
                        )
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc):
                        raise
                    with lock_errors_lock:
                        lock_errors += 1
        except BaseException as exc:  # noqa: BLE001 - captured for the main thread
            other_errors.append(exc)
        finally:
            con.close()

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert other_errors == []
    assert lock_errors > 0
