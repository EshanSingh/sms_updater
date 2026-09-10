# Testudo Watch — Phase 2 Implementation Plan (Read-Only Web Dashboard)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `testudo-watch serve` — a local FastAPI app that reads the existing SQLite database and renders an auto-refreshing dashboard of watches, per-section seat counts, recent notifications, and watcher health. Two-process: the poll loop (`testudo-watch run`) is unchanged except for an additive per-cycle heartbeat write.

**Architecture:** A new `web.py` (app factory, pure view-model builders, routes) plus `web_time.py` (time helpers) and four Jinja2 templates refreshed by HTMX. `db.py` schema goes to v2 with two additive tables (`watcher_heartbeat`, `watch_health`) and gains a read-only open mode plus read/write helpers. `engine.py` writes the heartbeat and per-watch health each cycle — nothing else about it changes. The web layer only ever SELECTs; a missing or un-migrated database renders a guidance page at HTTP 200.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn[standard], Jinja2, HTMX (CDN), stdlib `sqlite3` / `datetime`. Tests: pytest + `fastapi.testclient.TestClient` (needs `httpx`).

**Spec:** `docs/superpowers/specs/2026-09-10-testudo-watch-phase2-design.md`

## Global Constraints

- Python 3.11+. Dependencies after this phase are exactly: runtime `requests`, `beautifulsoup4`, `lxml`, `twilio`, `python-dotenv`, `fastapi`, `uvicorn[standard]`, `jinja2`; dev `pytest`, `httpx`. Add nothing else.
- All source under `src/testudo_watch/`; all tests under `tests/`. Templates under `src/testudo_watch/templates/`.
- `db.SCHEMA_VERSION` is `2`. New tables are `CREATE TABLE IF NOT EXISTS` appended to `_SCHEMA`; `migrate()` is otherwise untouched.
- The web layer is **read-only**: it opens the DB with `Database(path, read_only=True)`, never writes, never calls `migrate()`. Any `sqlite3.OperationalError` while opening or reading (missing file, missing table on a pre-v2 DB) makes the route render the guidance page — HTTP 200, never 500.
- Engine changes are **additive**: detection, notification, de-dupe, `POLITE_DELAY`/jitter timing, and signal handling are unchanged. Every pre-existing `tests/test_engine.py` test must still pass **unmodified**.
- The server binds `127.0.0.1` only. No authentication.
- Every view-model builder takes an injected `now: datetime` (tz-aware UTC). Only route handlers call `datetime.now(timezone.utc)`.
- `pytest` output stays at **0 warnings** (the `filterwarnings` entry already covers bs4/lxml).
- Every code step is TDD: write the failing test, run it and see it fail, implement minimally, run it and see it pass, commit.

---

## File Structure

**Created:**
- `src/testudo_watch/web_time.py` — `parse_db_utc`, `humanize_age` (pure, no deps)
- `src/testudo_watch/web.py` — `STALE_GRACE_SECONDS`, view-model dataclasses, `build_status_view` / `build_watches_view` / `build_notifications_view`, `create_app`
- `src/testudo_watch/templates/dashboard.html` — full page: shell + HTMX + the three fragments included server-side
- `src/testudo_watch/templates/no_data.html` — full-page guidance when there is no readable DB
- `src/testudo_watch/templates/_status.html` — watcher-health fragment
- `src/testudo_watch/templates/_watches.html` — watches + seat-counts fragment
- `src/testudo_watch/templates/_notifications.html` — recent-notifications fragment
- `src/testudo_watch/templates/_no_data.html` — guidance fragment (used by `no_data.html` and the fragment routes)
- `tests/test_web_time.py`
- `tests/test_web_views.py` — unit tests for the three builders
- `tests/test_web.py` — `TestClient` tests for the routes

**Modified:**
- `pyproject.toml` — add the four new deps + template package-data
- `src/testudo_watch/db.py` — `SCHEMA_VERSION` → 2; two tables; `Heartbeat` / `WatchHealth` / `SectionRow` / `NotificationRow` dataclasses; `read_only` open mode; `write_heartbeat`, `get_heartbeat`, `upsert_watch_health`, `get_watch_health`, `get_section_rows`, `recent_notifications`
- `src/testudo_watch/engine.py` — capture the scrape exception; `db.upsert_watch_health(...)` on both paths; `cycle_count` + `db.write_heartbeat(...)` each cycle
- `src/testudo_watch/cli.py` — `serve` subcommand
- `tests/test_db.py` — new cases for the v2 additions
- `tests/test_engine.py` — new cases asserting heartbeat + health rows (existing cases untouched)
- `tests/test_cli.py` — `serve` wiring
- `README.md` — "Web dashboard" section

**Deleted:** none.

---

## Task 1: Dependencies and packaging

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `import fastapi`, `import uvicorn`, `import jinja2`, `import httpx` all succeed; template files ship in a wheel.

- [ ] **Step 1: Add the runtime dependencies**

In `pyproject.toml`, change the `[project].dependencies` list to:

```toml
dependencies = [
    "requests>=2.31",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "twilio>=9.0",
    "python-dotenv>=1.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "jinja2>=3.1",
]
```

- [ ] **Step 2: Add the dev dependency**

Change `[project.optional-dependencies]` to:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.27"]
```

- [ ] **Step 3: Ship the templates in built distributions**

Add this block to `pyproject.toml` (anywhere after `[tool.setuptools.packages.find]`):

```toml
[tool.setuptools.package-data]
testudo_watch = ["templates/*.html"]
```

- [ ] **Step 4: Reinstall and verify**

Run:
```bash
python -m pip install -e ".[dev]"
python -c "import fastapi, uvicorn, jinja2, httpx; print('deps ok')"
python -m pytest -q
```
Expected: install succeeds; `deps ok`; the full existing suite passes with 0 warnings (53 tests as of Phase 1 + the waitlist fix).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add fastapi/uvicorn/jinja2 (+ httpx dev) for the web dashboard"
```

---

## Task 2: Database — schema v2, dataclasses, read-only mode, helpers

**Files:**
- Modify: `src/testudo_watch/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Watch`, `SectionSnapshot` from `testudo_watch.models`.
- Produces:
  - `SCHEMA_VERSION == 2`; tables `watcher_heartbeat`, `watch_health`.
  - Frozen dataclasses `Heartbeat(updated_at: str, cycle_count: int)`, `WatchHealth(consecutive_failures: int, last_success_at: str | None, last_error: str | None, last_error_at: str | None)`, `SectionRow(section_id: str, total_seats: int, open_seats: int, waitlist: int, updated_at: str)`, `NotificationRow(sent_at, course_id, term_id, section_id, open_seats, channel, status, detail)` (all `str` except `open_seats: int`).
  - `Database(path, *, read_only: bool = False)` — read-only mode opens `file:<abs-path>?mode=ro` and skips `migrate()`.
  - `write_heartbeat(cycle_count: int) -> None`, `get_heartbeat() -> Heartbeat | None`.
  - `upsert_watch_health(watch: Watch, *, ok: bool, error: str = "") -> None`, `get_watch_health() -> dict[tuple[str, str], WatchHealth]`.
  - `get_section_rows(watch: Watch) -> list[SectionRow]` (ordered by `section_id`).
  - `recent_notifications(limit: int = 50) -> list[NotificationRow]` (newest first).

- [ ] **Step 1: Write the failing tests — append to `tests/test_db.py`**

Add these imports at the top of the file (merge with the existing import lines):

```python
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
```

Append these tests:

```python
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


def test_read_only_open_does_not_write_user_version(tmp_path):
    handmade = tmp_path / "v0.db"
    con = sqlite3.connect(str(handmade))
    con.execute("CREATE TABLE placeholder (x INTEGER)")  # make it a real file
    con.commit()
    con.close()  # user_version is still 0 (CREATE TABLE does not bump it)
    ro = Database(handmade, read_only=True)
    assert ro.connection.execute("PRAGMA user_version").fetchone()[0] == 0
    ro.close()
```

If `tests/test_db.py` already has a test that seeds `PRAGMA user_version = 99` and expects `DatabaseError`, leave it as-is — `99 > 2` still holds. If it seeds a value `<= 2`, change that literal to `99`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_db.py -q`
Expected: FAIL — `ImportError: cannot import name 'Heartbeat'` (and the new tests error on the missing methods).

- [ ] **Step 3: Add the dataclasses and imports to `src/testudo_watch/db.py`**

Add to the imports:

```python
from dataclasses import dataclass
```

Immediately after `class DatabaseError(Exception): ...`, add:

```python
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
```

- [ ] **Step 4: Bump the version and add the tables**

Change `SCHEMA_VERSION = 1` to `SCHEMA_VERSION = 2`.

Append to the `_SCHEMA` string (before its closing `"""`):

```sql

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
```

- [ ] **Step 5: Add the read-only open mode**

Replace `Database.__init__` with:

```python
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
```

- [ ] **Step 6: Add the six helper methods**

Add these methods to `Database` (after `record_notification`):

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_db.py -q`
Expected: all pass (the new cases plus every pre-existing `test_db.py` case).

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings. (Pre-existing `test_engine.py` tests use a real `Database` and are unaffected — the new methods are not called yet.)

- [ ] **Step 9: Commit**

```bash
git add src/testudo_watch/db.py tests/test_db.py
git commit -m "feat(db): schema v2 with watcher_heartbeat/watch_health, read-only open, web read helpers"
```

---

## Task 3: Engine — write heartbeat and per-watch health each cycle

**Files:**
- Modify: `src/testudo_watch/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Database.write_heartbeat`, `Database.upsert_watch_health` (Task 2).
- Produces: no signature changes. After a `run(...)` (any mode) the DB has a `watcher_heartbeat` row; after each watch is processed its `watch_health` row reflects success (`consecutive_failures = 0`, `last_success_at` set) or failure (`consecutive_failures` incremented, `last_error` set).

- [ ] **Step 1: Write the failing tests — append to `tests/test_engine.py`**

Reuse the file's existing `cfg()`, `snap()`, `FakeNotifier`, and imports. Append:

```python
def test_run_once_writes_a_heartbeat(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    fetch = lambda s, c, t: [snap("CMSC351", "0101", 6)]
    run(cfg(), db, FakeNotifier(), None, once=True, fetch=fetch, sleep=lambda s: None)
    hb = db.get_heartbeat()
    assert hb is not None and hb.cycle_count == 1
    db.close()


def test_run_records_watch_health_on_success(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    fetch = lambda s, c, t: [snap("CMSC351", "0101", 6)]
    run(cfg(), db, FakeNotifier(), None, once=True, fetch=fetch, sleep=lambda s: None)
    h = db.get_watch_health()[("CMSC351", "202601")]
    assert h.consecutive_failures == 0 and h.last_success_at is not None
    db.close()


def test_run_records_watch_health_on_scrape_failure(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])

    def boom(session, course_id, term_id):
        raise ScrapeError("testudo down")

    run(cfg(), db, FakeNotifier(), None, once=True, fetch=boom, sleep=lambda s: None)
    h = db.get_watch_health()[("CMSC351", "202601")]
    assert h.consecutive_failures == 1 and h.last_error == "testudo down"
    db.close()
```

If `ScrapeError` is not already imported in `tests/test_engine.py`, add `from testudo_watch.scraper import ScrapeError`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -q -k "heartbeat or watch_health"`
Expected: FAIL — no heartbeat row / no `watch_health` row is written yet.

- [ ] **Step 3: Capture the exception and record failure health in `_process_watch`**

In `src/testudo_watch/engine.py`, change the `except` line in `_process_watch` from

```python
    except (ScrapeError, requests.RequestException):
        _log.error("scrape failed for %s", key, exc_info=True)
        failure_counts[key] = failure_counts.get(key, 0) + 1
```

to

```python
    except (ScrapeError, requests.RequestException) as exc:
        _log.error("scrape failed for %s", key, exc_info=True)
        failure_counts[key] = failure_counts.get(key, 0) + 1
        db.upsert_watch_health(watch, ok=False, error=str(exc))
```

- [ ] **Step 4: Record success health**

Immediately after the existing `failure_counts[key] = 0` line (the success path, just before `previous = db.get_snapshots(watch)`), add:

```python
    db.upsert_watch_health(watch, ok=True)
```

- [ ] **Step 5: Add the cycle counter and heartbeat write in `run`**

In `run`, inside the `try:` block, change

```python
    try:
        while True:
            for index, watch in enumerate(db.get_active_watches()):
                if _stop:
                    return
                if index > 0:
                    # polite delay BETWEEN watches, in both once and loop modes
                    sleep(POLITE_DELAY_SECONDS)
                _process_watch(
                    config, db, notifier, session, watch, fetch, failure_counts
                )
            if once or _stop:
                return
```

to

```python
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
```

- [ ] **Step 6: Run the engine tests**

Run: `python -m pytest tests/test_engine.py -q`
Expected: the three new tests pass AND every pre-existing `test_engine.py` test still passes unchanged.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 8: Commit**

```bash
git add src/testudo_watch/engine.py tests/test_engine.py
git commit -m "feat(engine): write watcher heartbeat and per-watch health each cycle"
```

---

## Task 4: `web_time.py` — pure time helpers

**Files:**
- Create: `src/testudo_watch/web_time.py`, `tests/test_web_time.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `parse_db_utc(s: str) -> datetime` — parses the DB's `"%Y-%m-%d %H:%M:%S"` string as tz-aware UTC.
  - `humanize_age(then: datetime, now: datetime) -> str` — `"8s ago"` / `"4m ago"` / `"3h ago"` / `"3d ago"`; negative deltas clamp to `"0s ago"`.

- [ ] **Step 1: Write the failing test — `tests/test_web_time.py`**

```python
from datetime import datetime, timezone

from testudo_watch.web_time import humanize_age, parse_db_utc


def _utc(y, mo, d, h, mi, s):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def test_parse_db_utc_returns_tz_aware_utc():
    dt = parse_db_utc("2026-09-10 04:28:24")
    assert dt.tzinfo is timezone.utc
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
        2026,
        9,
        10,
        4,
        28,
        24,
    )


def test_humanize_age_across_unit_boundaries():
    now = _utc(2026, 9, 10, 12, 0, 0)
    assert humanize_age(_utc(2026, 9, 10, 11, 59, 52), now) == "8s ago"
    assert humanize_age(_utc(2026, 9, 10, 11, 56, 0), now) == "4m ago"
    assert humanize_age(_utc(2026, 9, 10, 9, 0, 0), now) == "3h ago"
    assert humanize_age(_utc(2026, 9, 7, 12, 0, 0), now) == "3d ago"


def test_humanize_age_clamps_future_to_zero():
    now = _utc(2026, 9, 10, 12, 0, 0)
    assert humanize_age(_utc(2026, 9, 10, 12, 0, 5), now) == "0s ago"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_web_time.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'testudo_watch.web_time'`.

- [ ] **Step 3: Write `src/testudo_watch/web_time.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone

_DB_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_db_utc(s: str) -> datetime:
    return datetime.strptime(s, _DB_FORMAT).replace(tzinfo=timezone.utc)


def humanize_age(then: datetime, now: datetime) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_web_time.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/web_time.py tests/test_web_time.py
git commit -m "feat(web): add UTC parse + humanize_age time helpers"
```

---

## Task 5: `web.py` — view-model builders

**Files:**
- Create: `src/testudo_watch/web.py` (builders + dataclasses only this task), `tests/test_web_views.py`

**Interfaces:**
- Consumes: `AppConfig` (`testudo_watch.config`); `Database` and its Task-2 read helpers; `parse_db_utc` / `humanize_age` (`testudo_watch.web_time`).
- Produces:
  - `STALE_GRACE_SECONDS = 20`.
  - Frozen dataclasses `UnhealthyWatch`, `StatusView`, `SectionView`, `WatchView`, `NotificationView` (fields exactly as written below).
  - `build_status_view(db: Database, config: AppConfig, *, now: datetime) -> StatusView`.
  - `build_watches_view(db: Database, *, now: datetime) -> list[WatchView]`.
  - `build_notifications_view(db: Database, *, now: datetime, limit: int = 50) -> list[NotificationView]`.
  - Filtering rule: when `watch.sections` is non-empty, `build_watches_view` shows only those section ids (matches `detect_openings` semantics); when empty, it shows every known section.
  - Staleness rule: `stale` is true when there is no heartbeat, or `now - heartbeat.updated_at > 2 * config.poll_interval_seconds + STALE_GRACE_SECONDS`.

- [ ] **Step 1: Write the failing tests — `tests/test_web_views.py`**

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_web_views.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'testudo_watch.web'`.

- [ ] **Step 3: Write `src/testudo_watch/web.py` (builders portion)**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.web_time import humanize_age, parse_db_utc

STALE_GRACE_SECONDS = 20


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
    unhealthy = [
        UnhealthyWatch(c, t, h.consecutive_failures, h.last_error)
        for (c, t), h in sorted(health.items())
        if h.consecutive_failures > 0
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
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_web_views.py -q`
Expected: 8 passed.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 6: Commit**

```bash
git add src/testudo_watch/web.py tests/test_web_views.py
git commit -m "feat(web): view-model builders for status, watches, notifications"
```

---

## Task 6: `web.py` — FastAPI app, routes, and templates

**Files:**
- Modify: `src/testudo_watch/web.py` (add `create_app` + route handlers)
- Create: `src/testudo_watch/templates/dashboard.html`, `no_data.html`, `_status.html`, `_watches.html`, `_notifications.html`, `_no_data.html`
- Create: `tests/test_web.py`

**Interfaces:**
- Consumes: the Task-5 builders; `Database(path, read_only=True)`.
- Produces: `create_app(config: AppConfig) -> fastapi.FastAPI` with routes `GET /` (full page), `GET /fragments/status`, `GET /fragments/watches`, `GET /fragments/notifications`. Every route returns HTTP 200. When the DB cannot be opened or read (`sqlite3.OperationalError`), `/` renders `no_data.html` and the fragment routes render `_no_data.html`.

- [ ] **Step 1: Write the failing tests — `tests/test_web.py`**

```python
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot, Watch
from testudo_watch.web import create_app


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path=str(db_path),
        log_dir="logs",
    )


def seed(tmp_path, *, heartbeat_at=None, cycle=42):
    hb = heartbeat_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.connection.execute(
        "INSERT INTO section_snapshots "
        "(course_id, term_id, section_id, total_seats, open_seats, waitlist, updated_at) "
        "VALUES ('CMSC351','202601','0101',200,4,0,?)",
        (hb,),
    )
    db.connection.execute(
        "INSERT INTO watcher_heartbeat (id, updated_at, cycle_count) VALUES (1, ?, ?)",
        (hb, cycle),
    )
    db.record_notification(
        SectionSnapshot("CMSC351", "202601", "0101", 4, 0, 0),
        channel="console",
        status="sent",
    )
    db.connection.commit()
    db.close()


def test_dashboard_renders_seeded_data(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "CMSC351" in body and "0101" in body and "cycle" in body


def test_fragment_routes_return_bare_partials(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    for path in ("/fragments/status", "/fragments/watches", "/fragments/notifications"):
        r = client.get(path)
        assert r.status_code == 200
        assert "<html" not in r.text.lower()


def test_status_fragment_shows_stale_for_old_heartbeat(tmp_path):
    stale_at = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    seed(tmp_path, heartbeat_at=stale_at)
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    r = client.get("/fragments/status")
    assert r.status_code == 200
    assert "stopped" in r.text.lower()


def test_status_fragment_healthy_for_fresh_heartbeat(tmp_path):
    seed(tmp_path)  # heartbeat ≈ now
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    r = client.get("/fragments/status")
    assert "cycle" in r.text and "42" in r.text
    assert "stopped" not in r.text.lower()


def test_missing_database_shows_guidance_not_500(tmp_path):
    client = TestClient(create_app(cfg(tmp_path / "missing.db")))
    r = client.get("/")
    assert r.status_code == 200 and "testudo-watch run" in r.text
    r2 = client.get("/fragments/watches")
    assert r2.status_code == 200 and "<html" not in r2.text.lower()


def test_pre_v2_database_shows_guidance(tmp_path):
    p = tmp_path / "v1.db"
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE watches (course_id TEXT, term_id TEXT, "
        "sections_csv TEXT, active INT, PRIMARY KEY(course_id, term_id))"
    )
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    client = TestClient(create_app(cfg(str(p))))
    r = client.get("/")
    assert r.status_code == 200 and "testudo-watch run" in r.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_web.py -q`
Expected: FAIL — `ImportError: cannot import name 'create_app'`.

- [ ] **Step 3: Write the six templates**

`src/testudo_watch/templates/dashboard.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>testudo-watch</title>
<script src="https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js"></script>
<style>
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 2rem auto; padding: 0 1rem; }
  h2 { margin-top: 1.75rem; }
  .banner { background: #fdf0d5; border: 1px solid #e0b84c; padding: .5rem .75rem; border-radius: 4px; margin: .4rem 0; }
  .dot { display: inline-block; width: .6rem; height: .6rem; border-radius: 50%; }
  .dot.open { background: #2e9e4f; }
  .dot.closed { background: #c0392b; }
  table { border-collapse: collapse; width: 100%; }
  td, th { text-align: left; padding: .25rem .5rem; border-bottom: 1px solid #eee; }
  .muted { color: #666; font-size: .85em; }
  .badge { font-size: .8em; padding: .1rem .4rem; border-radius: 3px; background: #eee; }
  .badge.sent { background: #d7efdc; }
  .badge.failed, .badge.health-failed { background: #f6cccc; }
</style>
</head>
<body>
<h1>testudo-watch</h1>
<div hx-get="/fragments/status" hx-trigger="every 15s" hx-swap="innerHTML">
  {% include "_status.html" %}
</div>
<h2>Watches</h2>
<div hx-get="/fragments/watches" hx-trigger="every 15s" hx-swap="innerHTML">
  {% include "_watches.html" %}
</div>
<h2>Recent notifications</h2>
<div hx-get="/fragments/notifications" hx-trigger="every 15s" hx-swap="innerHTML">
  {% include "_notifications.html" %}
</div>
</body>
</html>
```

`src/testudo_watch/templates/_status.html`:

```html
{% if not status.has_data %}
  <p class="banner">Watcher has not completed a poll yet &mdash; run <code>testudo-watch run</code>.</p>
{% else %}
  <p{% if status.stale %} class="banner"{% endif %}>
    Watcher: last poll <strong>{{ status.last_poll_age }}</strong> &middot;
    cycle <strong>{{ status.cycle_count }}</strong>{% if status.stale %} &mdash; may be stopped{% endif %}
  </p>
{% endif %}
{% for u in status.unhealthy %}
  <p class="banner">&#9888; {{ u.course_id }} {{ u.term_id }} &mdash;
    {{ u.consecutive_failures }} consecutive failure(s){% if u.last_error %}: {{ u.last_error }}{% endif %}</p>
{% endfor %}
```

`src/testudo_watch/templates/_watches.html`:

```html
{% if not watches %}
  <p class="muted">No watches configured yet.</p>
{% endif %}
{% for w in watches %}
  <h3>{{ w.course_id }} <span class="muted">{{ w.term_id }} &middot; {{ w.section_label }}</span></h3>
  {% if not w.sections %}
    <p class="muted">No sections seen yet.</p>
  {% else %}
  <table>
    <tr><th>Section</th><th>Open</th><th>Total</th><th>Waitlist</th><th></th></tr>
    {% for s in w.sections %}
    <tr>
      <td>{{ s.section_id }}</td>
      <td><span class="dot {{ 'open' if s.is_open else 'closed' }}"></span> {{ s.open_seats }}</td>
      <td>{{ s.total_seats }}</td>
      <td>{{ s.waitlist }}</td>
      <td class="muted">updated {{ s.updated_age }}</td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
{% endfor %}
```

`src/testudo_watch/templates/_notifications.html`:

```html
{% if not notifications %}
  <p class="muted">No notifications yet.</p>
{% else %}
<table>
  {% for n in notifications %}
  <tr>
    <td class="muted">{{ n.sent_age }}</td>
    <td>{{ n.course_id }} {{ n.section_id }}</td>
    <td>{{ n.open_seats }} open</td>
    <td class="muted">{{ n.channel }}</td>
    <td><span class="badge {{ n.status }}">{{ n.status }}</span></td>
  </tr>
  {% endfor %}
</table>
{% endif %}
```

`src/testudo_watch/templates/_no_data.html`:

```html
<p class="banner">No watcher data yet. Start it with <code>testudo-watch run</code>, then refresh.</p>
```

`src/testudo_watch/templates/no_data.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>testudo-watch</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 2rem auto; padding: 0 1rem; }
  .banner { background: #fdf0d5; border: 1px solid #e0b84c; padding: .5rem .75rem; border-radius: 4px; }
  code { background: #f2f2f2; padding: 0 .2rem; }
</style>
</head>
<body>
<h1>testudo-watch</h1>
{% include "_no_data.html" %}
</body>
</html>
```

- [ ] **Step 4: Add `create_app` and the routes to `src/testudo_watch/web.py`**

Change Task 5's `from datetime import datetime` line to:

```python
from datetime import datetime, timezone
```

and add these imports below the existing ones in `web.py`:

```python
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
```

At the end of `web.py`, add:

```python
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="testudo-watch")

    def _load_views() -> dict | None:
        now = datetime.now(timezone.utc)
        try:
            db = Database(config.db_path, read_only=True)
        except sqlite3.OperationalError:
            return None
        try:
            return {
                "status": build_status_view(db, config, now=now),
                "watches": build_watches_view(db, now=now),
                "notifications": build_notifications_view(db, now=now),
            }
        except sqlite3.OperationalError:
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

    return app
```

- [ ] **Step 5: Run the web tests**

Run: `python -m pytest tests/test_web.py -q`
Expected: 6 passed. If `TemplateResponse(request, name, context)` raises a signature error on the installed Starlette, use the legacy form `TemplateResponse(name, {"request": request, **context})` consistently in all five call sites.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add src/testudo_watch/web.py src/testudo_watch/templates tests/test_web.py
git commit -m "feat(web): FastAPI dashboard app, routes, and HTMX templates"
```

---

## Task 7: CLI `serve` subcommand + README

**Files:**
- Modify: `src/testudo_watch/cli.py`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `create_app` (`testudo_watch.web`), `uvicorn`.
- Produces: `testudo-watch serve [--config watches.toml] [--db PATH] [--host 127.0.0.1] [--port 8477]` — starts uvicorn on `create_app(config)`. No `--verbose`. Exit codes: 0 (normal / Ctrl-C), 1 (`ConfigError` from `_load`), 2 (unexpected).

- [ ] **Step 1: Write the failing tests — append to `tests/test_cli.py`**

Reuse the file's existing `cli` import and `write_config` helper. Append:

```python
def test_serve_parser_builds_without_verbose():
    from testudo_watch.cli import _build_parser

    args = _build_parser().parse_args(
        ["serve", "--host", "0.0.0.0", "--port", "9001"]
    )
    assert args.command == "serve"
    assert args.host == "0.0.0.0" and args.port == 9001
    assert not hasattr(args, "verbose")


def test_serve_invokes_uvicorn_with_parsed_host_and_port(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    seen = {}

    def fake_run(app, host, port):
        seen["app"] = app
        seen["host"] = host
        seen["port"] = port

    def fake_create_app(config):
        seen["config"] = config
        return object()

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("testudo_watch.web.create_app", fake_create_app)

    rc = cli.main(["serve", "--config", str(config_path), "--port", "1234"])
    assert rc == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 1234
    assert seen["config"].poll_interval_seconds == 30
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_cli.py -q -k serve`
Expected: FAIL — `serve` is not a valid subcommand yet (`SystemExit: 2` from argparse).

- [ ] **Step 3: Add `serve` to the parser**

In `src/testudo_watch/cli.py`, replace `_build_parser` with:

```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testudo-watch")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "check-once", "list", "serve"):
        p = sub.add_parser(name)
        p.add_argument("--config", default="watches.toml")
        p.add_argument("--db", default=None)
        if name not in ("list", "serve"):
            p.add_argument("--verbose", action="store_true")
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", default=8477, type=int)
    return parser
```

- [ ] **Step 4: Add the `serve` command handler**

Add this function to `cli.py` (next to `_cmd_list` / `_cmd_run`):

```python
def _cmd_serve(config, *, host: str, port: int) -> int:
    import uvicorn

    from testudo_watch.web import create_app

    uvicorn.run(create_app(config), host=host, port=port)
    return 0
```

- [ ] **Step 5: Dispatch `serve` in `main`**

In `main`, immediately after the `if args.command == "list":` block, add:

```python
    if args.command == "serve":
        configure_logging(config.log_dir)
        try:
            return _cmd_serve(config, host=args.host, port=args.port)
        except KeyboardInterrupt:
            return 0
        except Exception:  # noqa: BLE001
            _log.exception("fatal error")
            return 2
```

- [ ] **Step 6: Run the CLI tests**

Run: `python -m pytest tests/test_cli.py -q`
Expected: all pass (the two new `serve` tests plus every pre-existing `test_cli.py` test).

- [ ] **Step 7: Add the README section**

In `README.md`, after the existing CLI usage section, add:

```markdown
## Web dashboard

With the watcher running (`testudo-watch run`), start the read-only dashboard in
another terminal:

```bash
testudo-watch serve
```

Open <http://127.0.0.1:8477/>. It shows each configured watch with live
per-section seat counts, recent notifications, and when the watcher last polled
(with a warning banner if it looks stopped). It only reads
`testudo_watch.db` — it never edits your watches or sends anything. Override the
bind address with `--host` / `--port`.
```

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 9: Manual smoke test (network + two terminals)**

1. Terminal A: set a current `term_id` in `watches.toml`, then `testudo-watch run`.
2. Terminal B: `testudo-watch serve`, open <http://127.0.0.1:8477/>.
   Expected: watches listed with seat counts; "last poll Ns ago · cycle N";
   notifications appear as sections open.
3. Stop Terminal A (Ctrl-C). Within ~2 poll intervals the dashboard status line
   gets the amber "may be stopped" banner.
4. Stop Terminal B, delete `testudo_watch.db`, `testudo-watch serve` again,
   reload: the guidance page renders (HTTP 200), not an error.

- [ ] **Step 10: Commit**

```bash
git add src/testudo_watch/cli.py tests/test_cli.py README.md
git commit -m "feat(cli): add `testudo-watch serve` for the web dashboard; document it"
```

---

## Self-Review Notes

**Spec coverage:**
- Two-process model, `serve` command → Task 7.
- New deps (`fastapi`, `uvicorn[standard]`, `jinja2`, dev `httpx`), template packaging → Task 1.
- `SCHEMA_VERSION` 2, `watcher_heartbeat` + `watch_health`, `read_only` open mode, `write_heartbeat` / `get_heartbeat` / `upsert_watch_health` / `get_watch_health` / `get_section_rows` / `recent_notifications`, dataclasses in `db.py` → Task 2.
- Engine writes heartbeat before the `once`/`_stop` return; per-watch health on both paths; additive only → Task 3.
- `parse_db_utc` + `humanize_age`, `now` injected → Task 4.
- `STALE_GRACE_SECONDS`, staleness formula `2·interval + grace`, `build_*_view`, section filtering matching `detect_openings` → Task 5.
- `create_app`, 4 routes, `sqlite3.OperationalError` → guidance page at HTTP 200, first-paint server-rendered fragments + HTMX `every 15s` → Task 6.
- Degraded states (missing DB, pre-v2 DB, no heartbeat, stale, unhealthy) → Tasks 5 (view logic) + 6 (route/template rendering).
- `README` "Web dashboard" section → Task 7.
- Testing strategy (`test_web_time`, `test_web_views`, `test_web`, plus `test_db` / `test_engine` / `test_cli` additions) → distributed across tasks.

**Clarification made against the spec:** the spec's `build_watches_view` description said "per section" without stating a filter; this plan filters to `watch.sections` when non-empty (Task 5), matching `detect_openings`. `build_status_view` orders `unhealthy` by `(course_id, term_id)` for deterministic rendering — the spec did not specify an order.

**Placeholder scan:** no `TBD` / `TODO`. Every code step carries the full code. Template HTML is complete. The one conditional instruction (Step 5 of Task 6: fall back to the legacy `TemplateResponse` form) names the exact alternative call shape.

**Type consistency:** `Database` read helpers return the dataclasses named identically in Task 2's Interfaces and consumed in Task 5 (`get_heartbeat`→`Heartbeat`, `get_watch_health`→`dict[tuple[str,str], WatchHealth]`, `get_section_rows`→`list[SectionRow]`, `recent_notifications`→`list[NotificationRow]`). `build_status_view(db, config, *, now)` / `build_watches_view(db, *, now)` / `build_notifications_view(db, *, now, limit=50)` signatures match between Task 5 and Task 6's `_load_views`. `create_app(config)` matches between Task 6 and Task 7's `_cmd_serve`. `read_only` keyword matches between Task 2 (`db.py`) and Task 6 (`_load_views`). `write_heartbeat(cycle_count)` / `upsert_watch_health(watch, *, ok, error="")` match between Task 2 and Task 3.
