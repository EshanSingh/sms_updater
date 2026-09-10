from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from testudo_watch.models import SectionSnapshot, Watch

SCHEMA_VERSION = 2


class DatabaseError(Exception):
    """Raised when the on-disk database cannot be used by this version."""


@dataclass(frozen=True)
class Heartbeat:
    updated_at: str
    cycle_count: int


@dataclass(frozen=True)
class WatchHealth:
    consecutive_failures: int
    last_success_at: str | None
    last_error: str | None
    last_error_at: str | None


@dataclass(frozen=True)
class SectionRow:
    section_id: str
    total_seats: int
    open_seats: int
    waitlist: int
    updated_at: str


@dataclass(frozen=True)
class NotificationRow:
    sent_at: str
    course_id: str
    term_id: str
    section_id: str
    open_seats: int
    channel: str
    status: str
    detail: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    course_id    TEXT NOT NULL,
    term_id      TEXT NOT NULL,
    sections_csv TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (course_id, term_id)
);

CREATE TABLE IF NOT EXISTS section_snapshots (
    course_id   TEXT NOT NULL,
    term_id     TEXT NOT NULL,
    section_id  TEXT NOT NULL,
    total_seats INTEGER NOT NULL,
    open_seats  INTEGER NOT NULL,
    waitlist    INTEGER NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (course_id, term_id, section_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  TEXT NOT NULL,
    term_id    TEXT NOT NULL,
    section_id TEXT NOT NULL,
    open_seats INTEGER NOT NULL,
    sent_at    TEXT NOT NULL DEFAULT (datetime('now')),
    channel    TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watcher_heartbeat (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at  TEXT NOT NULL,
    cycle_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_health (
    course_id            TEXT NOT NULL,
    term_id              TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success_at      TEXT,
    last_error           TEXT,
    last_error_at        TEXT,
    PRIMARY KEY (course_id, term_id)
);
"""


class Database:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = str(path)
        if read_only:
            uri = Path(self.path).resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.row_factory = sqlite3.Row
        else:
            self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            self.migrate()

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        found = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if found > SCHEMA_VERSION:
            raise DatabaseError(
                f"database file {self.path} was written by a newer testudo-watch "
                f"(schema v{found} > v{SCHEMA_VERSION}); upgrade the package"
            )
        # found == 0 (fresh/unversioned) or found == SCHEMA_VERSION: proceed.
        # Future migrations for the 1..SCHEMA_VERSION-1 range go here.
        self.connection.executescript(_SCHEMA)
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    def sync_watches(self, watches: list[Watch]) -> None:
        keep = {(w.course_id, w.term_id) for w in watches}
        with self.connection:
            for w in watches:
                self.connection.execute(
                    "INSERT INTO watches (course_id, term_id, sections_csv, active) "
                    "VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(course_id, term_id) DO UPDATE SET "
                    "sections_csv = excluded.sections_csv, active = 1",
                    (w.course_id, w.term_id, ",".join(w.sections)),
                )
            for row in self.connection.execute(
                "SELECT course_id, term_id FROM watches WHERE active = 1"
            ).fetchall():
                if (row["course_id"], row["term_id"]) not in keep:
                    self.connection.execute(
                        "UPDATE watches SET active = 0 "
                        "WHERE course_id = ? AND term_id = ?",
                        (row["course_id"], row["term_id"]),
                    )

    def get_active_watches(self) -> list[Watch]:
        rows = self.connection.execute(
            "SELECT course_id, term_id, sections_csv FROM watches "
            "WHERE active = 1 ORDER BY rowid"
        ).fetchall()
        result: list[Watch] = []
        for row in rows:
            csv = row["sections_csv"]
            sections = tuple(s for s in csv.split(",") if s) if csv else ()
            result.append(Watch(row["course_id"], row["term_id"], sections))
        return result

    def get_snapshots(self, watch: Watch) -> dict[str, SectionSnapshot]:
        rows = self.connection.execute(
            "SELECT course_id, term_id, section_id, total_seats, open_seats, waitlist "
            "FROM section_snapshots WHERE course_id = ? AND term_id = ?",
            (watch.course_id, watch.term_id),
        ).fetchall()
        return {
            row["section_id"]: SectionSnapshot(
                course_id=row["course_id"],
                term_id=row["term_id"],
                section_id=row["section_id"],
                total_seats=row["total_seats"],
                open_seats=row["open_seats"],
                waitlist=row["waitlist"],
            )
            for row in rows
        }

    def upsert_snapshots(self, snapshots: Iterable[SectionSnapshot]) -> None:
        with self.connection:
            for s in snapshots:
                self.connection.execute(
                    "INSERT INTO section_snapshots "
                    "(course_id, term_id, section_id, total_seats, open_seats, "
                    " waitlist, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
                    "ON CONFLICT(course_id, term_id, section_id) DO UPDATE SET "
                    "total_seats = excluded.total_seats, "
                    "open_seats = excluded.open_seats, "
                    "waitlist = excluded.waitlist, "
                    "updated_at = excluded.updated_at",
                    (
                        s.course_id,
                        s.term_id,
                        s.section_id,
                        s.total_seats,
                        s.open_seats,
                        s.waitlist,
                    ),
                )

    def record_notification(
        self,
        snapshot: SectionSnapshot,
        *,
        channel: str,
        status: str,
        detail: str = "",
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO notifications "
                "(course_id, term_id, section_id, open_seats, channel, status, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.course_id,
                    snapshot.term_id,
                    snapshot.section_id,
                    snapshot.open_seats,
                    channel,
                    status,
                    detail,
                ),
            )

    def write_heartbeat(self, cycle_count: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO watcher_heartbeat (id, updated_at, cycle_count) "
                "VALUES (1, datetime('now'), ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "updated_at = excluded.updated_at, "
                "cycle_count = excluded.cycle_count",
                (cycle_count,),
            )

    def get_heartbeat(self) -> Heartbeat | None:
        row = self.connection.execute(
            "SELECT updated_at, cycle_count FROM watcher_heartbeat WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return Heartbeat(updated_at=row["updated_at"], cycle_count=row["cycle_count"])

    def upsert_watch_health(
        self, watch: Watch, *, ok: bool, error: str = ""
    ) -> None:
        with self.connection:
            if ok:
                self.connection.execute(
                    "INSERT INTO watch_health "
                    "(course_id, term_id, consecutive_failures, last_success_at) "
                    "VALUES (?, ?, 0, datetime('now')) "
                    "ON CONFLICT(course_id, term_id) DO UPDATE SET "
                    "consecutive_failures = 0, "
                    "last_success_at = datetime('now')",
                    (watch.course_id, watch.term_id),
                )
            else:
                self.connection.execute(
                    "INSERT INTO watch_health (course_id, term_id, "
                    "consecutive_failures, last_error, last_error_at) "
                    "VALUES (?, ?, 1, ?, datetime('now')) "
                    "ON CONFLICT(course_id, term_id) DO UPDATE SET "
                    "consecutive_failures = watch_health.consecutive_failures + 1, "
                    "last_error = excluded.last_error, "
                    "last_error_at = excluded.last_error_at",
                    (watch.course_id, watch.term_id, error),
                )

    def get_watch_health(self) -> dict[tuple[str, str], WatchHealth]:
        rows = self.connection.execute(
            "SELECT course_id, term_id, consecutive_failures, last_success_at, "
            "last_error, last_error_at FROM watch_health"
        ).fetchall()
        return {
            (r["course_id"], r["term_id"]): WatchHealth(
                consecutive_failures=r["consecutive_failures"],
                last_success_at=r["last_success_at"],
                last_error=r["last_error"],
                last_error_at=r["last_error_at"],
            )
            for r in rows
        }

    def get_section_rows(self, watch: Watch) -> list[SectionRow]:
        rows = self.connection.execute(
            "SELECT section_id, total_seats, open_seats, waitlist, updated_at "
            "FROM section_snapshots WHERE course_id = ? AND term_id = ? "
            "ORDER BY section_id",
            (watch.course_id, watch.term_id),
        ).fetchall()
        return [
            SectionRow(
                section_id=r["section_id"],
                total_seats=r["total_seats"],
                open_seats=r["open_seats"],
                waitlist=r["waitlist"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def recent_notifications(self, limit: int = 50) -> list[NotificationRow]:
        rows = self.connection.execute(
            "SELECT sent_at, course_id, term_id, section_id, open_seats, "
            "channel, status, detail FROM notifications "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            NotificationRow(
                sent_at=r["sent_at"],
                course_id=r["course_id"],
                term_id=r["term_id"],
                section_id=r["section_id"],
                open_seats=r["open_seats"],
                channel=r["channel"],
                status=r["status"],
                detail=r["detail"],
            )
            for r in rows
        ]
