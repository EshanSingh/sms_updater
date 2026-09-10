from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from testudo_watch.models import SectionSnapshot, Watch

SCHEMA_VERSION = 1


class DatabaseError(Exception):
    """Raised when the on-disk database cannot be used by this version."""


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
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
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
