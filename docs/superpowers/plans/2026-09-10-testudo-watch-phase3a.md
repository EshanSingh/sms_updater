# Testudo Watch — Phase 3a Implementation Plan (Edit Watches from the UI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/watches` page to `testudo-watch serve` for adding, editing (section list), enabling/disabling, and deleting watches — stored in SQLite so they survive `testudo-watch run` restarts even though `watches.toml` auto-sync stays on.

**Architecture:** A per-watch `source` column (`'file'` | `'ui'`) on `watches`; `sync_watches` never touches `source='ui'` rows. Schema `v2 → v3` (first real `ALTER TABLE` migration). `serve` opens the DB read-write (like `run`) and gains WAL + a busy timeout so the two processes' writes coexist. New DB CRUD methods and FastAPI routes; every add/edit validates against a live Testudo probe.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2 + HTMX, stdlib `sqlite3`. Tests: pytest + `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-10-testudo-watch-phase3a-design.md`

## Global Constraints

- Python 3.11+. One new runtime dependency: `python-multipart` (required by Starlette/FastAPI to parse the HTML `<form>` POST bodies the `/watches` routes read — even `application/x-www-form-urlencoded`). Full runtime set becomes: fastapi, uvicorn[standard], jinja2, python-multipart, requests, beautifulsoup4, lxml, twilio, python-dotenv. Dev: pytest, httpx.
- All source under `src/testudo_watch/`; templates under `src/testudo_watch/templates/`; tests under `tests/`.
- `db.SCHEMA_VERSION` becomes `3`. The new `source` column is added to `_SCHEMA`'s `watches` CREATE **and** applied to existing DBs by an `ALTER TABLE` in `migrate()`, guarded by a `PRAGMA table_info(watches)` column check so it is idempotent. The `found > SCHEMA_VERSION → DatabaseError` guard stays.
- `source` is `'file'` or `'ui'`. Any mutating UI action sets the row `source='ui'`. `sync_watches` never updates or deactivates a `source='ui'` row.
- `serve` opens the DB **read-write** (`Database(config.db_path)`, no `read_only=True`). It therefore migrates/creates the DB on open, exactly like `run`. Read routes must not issue writes — enforced by a test, not a lock.
- `Database` write-mode connections run `PRAGMA journal_mode = WAL` and `PRAGMA busy_timeout = 5000`. Read-only connections run only `PRAGMA busy_timeout = 5000`.
- Add/edit validation: `course_id` normalized upper-case must match `^[A-Z]{4}\d{3}[A-Z]?$`; `term_id` must match `^\d{6}$`; then one `fetch_sections` probe — reject if it raises `ScrapeError` / `requests.RequestException`, or if a named section id is absent from the probe result. On rejection: HTTP 422, inline error, no DB write.
- `create_app(config, file_watches)` — `file_watches: list[Watch]` is the parsed `[[watch]]` list from `watches.toml`, used only by the delete route's file-guard.
- Delete route: 404 if the row is absent; if `(course_id, term_id)` is in `file_watches` → tombstone (`active=0, source='ui'`) + a message; else hard `delete_watch` (also clears that course/term's `watch_health` and `section_snapshots` rows).
- Full test suite stays at **0 warnings**.
- Every code step is TDD: failing test → see it fail → minimal implementation → see it pass → commit.

---

## File Structure

**Created:**
- `src/testudo_watch/templates/manage.html` — full `/watches` page (shell + nav + `#manage` container)
- `src/testudo_watch/templates/_manage.html` — add-form + watches-table fragment (returned by every mutation route)
- `tests/test_web_manage.py` — `/watches` page + add/edit/toggle/delete routes

**Modified:**
- `.gitignore` — add `*.db-wal`, `*.db-shm`
- `src/testudo_watch/db.py` — `SCHEMA_VERSION` → 3; `source` column; stepwise migration; WAL + `busy_timeout`; `WatchRow`; `get_all_watches`, `watch_row`, `add_or_replace_ui_watch`, `set_watch_sections`, `set_watch_active`, `delete_watch`; `sync_watches` respects `source`
- `src/testudo_watch/web.py` — `create_app(config, file_watches)`; read-write DB open; catch `testudo_watch.db.DatabaseError` too; `ManageRow` / `ManageView` / `build_manage_view`; `GET /watches` + `POST /watches` + `POST /watches/{c}/{t}/sections` + `.../active` + `.../delete`; probe imports
- `src/testudo_watch/templates/dashboard.html` — a nav line linking `Dashboard · Manage watches`
- `src/testudo_watch/cli.py` — `_cmd_serve(config, watches, *, host, port)` → `create_app(config, watches)`
- `tests/test_db.py` — v2→v3 migration, WAL pragma, `source` semantics in `sync_watches`, the new methods
- `tests/test_web.py` — update the 6 `create_app(cfg(...))` call sites to `create_app(cfg(...), [])`; rework the missing-DB / pre-v2 tests for read-write open
- `tests/test_cli.py` — `serve` wiring test: `fake_create_app` takes `(config, file_watches)`
- `README.md` — document the `/watches` page

**Deleted:** none.

---

## Task 1: DB schema v3 — `source` column, migration, WAL, source-aware `sync_watches`

**Files:**
- Modify: `src/testudo_watch/db.py`, `.gitignore`, `pyproject.toml`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `SCHEMA_VERSION == 3`; `watches` table has `source TEXT NOT NULL DEFAULT 'file'`.
  - `Database(path)` (write mode) has `PRAGMA journal_mode` == `'wal'` and `PRAGMA busy_timeout` == `5000`; `Database(path, read_only=True)` has `busy_timeout` == `5000`.
  - `sync_watches(file_watches)` inserts/updates rows as `source='file'`, never modifies or deactivates a `source='ui'` row.

- [ ] **Step 1: Write failing tests — append to `tests/test_db.py`**

Merge the imports (`Database`, `DatabaseError`, `Watch` are already imported). Append:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_db.py -q -k "v2_to_v3 or wal or source or sync_watches_ignores or ui_disabled or reopen_is_idempotent or wal_db_still"`
Expected: failures — `source` column missing, `journal_mode` is `delete`, sync touches the ui row.

- [ ] **Step 3: Add `source` to `_SCHEMA` and bump the version**

In `src/testudo_watch/db.py`: change `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3`. In `_SCHEMA`, change the `watches` table to:

```sql
CREATE TABLE IF NOT EXISTS watches (
    course_id    TEXT NOT NULL,
    term_id      TEXT NOT NULL,
    sections_csv TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    source       TEXT NOT NULL DEFAULT 'file',
    PRIMARY KEY (course_id, term_id)
);
```

- [ ] **Step 4: Add the stepwise migration + WAL to `Database`**

Replace `migrate` with:

```python
    def migrate(self) -> None:
        found = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if found > SCHEMA_VERSION:
            raise DatabaseError(
                f"database file {self.path} was written by a newer testudo-watch "
                f"(schema v{found} > v{SCHEMA_VERSION}); upgrade the package"
            )
        self.connection.executescript(_SCHEMA)
        cols = {
            r[1] for r in self.connection.execute("PRAGMA table_info(watches)")
        }
        if "source" not in cols:  # pre-v3 `watches` table already existed
            self.connection.execute(
                "ALTER TABLE watches ADD COLUMN source TEXT NOT NULL DEFAULT 'file'"
            )
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()
```

Change `__init__` to set the pragmas:

```python
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = str(path)
        if read_only:
            uri = Path(self.path).resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout = 5000")
        else:
            self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            self.migrate()
```

- [ ] **Step 5: Make `sync_watches` source-aware**

Replace `sync_watches` with:

```python
    def sync_watches(self, watches: list[Watch]) -> None:
        keep = {(w.course_id, w.term_id) for w in watches}
        with self.connection:
            for w in watches:
                self.connection.execute(
                    "INSERT INTO watches "
                    "(course_id, term_id, sections_csv, active, source) "
                    "VALUES (?, ?, ?, 1, 'file') "
                    "ON CONFLICT(course_id, term_id) DO UPDATE SET "
                    "sections_csv = excluded.sections_csv, active = 1 "
                    "WHERE watches.source = 'file'",
                    (w.course_id, w.term_id, ",".join(w.sections)),
                )
            for row in self.connection.execute(
                "SELECT course_id, term_id FROM watches "
                "WHERE active = 1 AND source = 'file'"
            ).fetchall():
                if (row["course_id"], row["term_id"]) not in keep:
                    self.connection.execute(
                        "UPDATE watches SET active = 0 "
                        "WHERE course_id = ? AND term_id = ?",
                        (row["course_id"], row["term_id"]),
                    )
```

- [ ] **Step 6: Update `.gitignore` and add the `python-multipart` dependency**

In `.gitignore`, add under the `# runtime artifacts` group:

```
*.db-wal
*.db-shm
```

In `pyproject.toml`, add `"python-multipart>=0.0.9",` to `[project.dependencies]`
(anywhere in the list; it's the FastAPI form-parsing dependency the `/watches`
routes need in Task 5). Then reinstall:

```bash
python -m pip install -e ".[dev]"
python -c "import multipart; print('multipart ok')"
```

- [ ] **Step 7: Run the db tests**

Run: `python -m pytest tests/test_db.py -q`
Expected: all pass, including every pre-existing `test_db.py` test (the `user_version = 99 → DatabaseError` test still holds; the Phase 2 `test_read_only_connection_rejects_writes` still holds). If `test_read_only_open_of_wal_db_still_works` fails at the *open* step on this SQLite build, STOP and report BLOCKED — read-only WAL open is unavailable and the design needs revisiting.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings. (`tests/test_web.py` still opens `read_only=True` via the current `create_app`; unaffected until Task 3.)

- [ ] **Step 9: Commit**

```bash
git add src/testudo_watch/db.py tests/test_db.py .gitignore pyproject.toml
git commit -m "feat(db): schema v3 — source column, WAL, source-aware sync_watches; add python-multipart"
```

---

## Task 2: DB — watch CRUD methods

**Files:**
- Modify: `src/testudo_watch/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `SCHEMA_VERSION == 3` and the `source` column (Task 1).
- Produces:
  - `WatchRow(course_id: str, term_id: str, sections: tuple[str, ...], active: bool, source: str)` — frozen dataclass.
  - `get_all_watches() -> list[WatchRow]` — active and inactive, `ORDER BY rowid`.
  - `watch_row(course_id: str, term_id: str) -> WatchRow | None`.
  - `add_or_replace_ui_watch(course_id: str, term_id: str, sections: tuple[str, ...]) -> None`.
  - `set_watch_sections(course_id: str, term_id: str, sections: tuple[str, ...]) -> None`.
  - `set_watch_active(course_id: str, term_id: str, active: bool) -> None`.
  - `delete_watch(course_id: str, term_id: str) -> None` — also deletes that course/term's `watch_health` and `section_snapshots` rows.

- [ ] **Step 1: Write failing tests — append to `tests/test_db.py`**

```python
from testudo_watch.db import WatchRow  # add to the existing db import line


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_db.py -q -k "ui_watch or watch_sections or watch_active or delete_watch or get_all_watches or watch_row"`
Expected: `ImportError: cannot import name 'WatchRow'` and `AttributeError` on the new methods.

- [ ] **Step 3: Add `WatchRow` and the methods to `db.py`**

Add near the other dataclasses (after `NotificationRow`):

```python
@dataclass(frozen=True)
class WatchRow:
    course_id: str
    term_id: str
    sections: tuple[str, ...]
    active: bool
    source: str
```

Add these methods to `Database` (after `get_active_watches`):

```python
    @staticmethod
    def _sections_tuple(csv: str) -> tuple[str, ...]:
        return tuple(s for s in csv.split(",") if s) if csv else ()

    def get_all_watches(self) -> list[WatchRow]:
        rows = self.connection.execute(
            "SELECT course_id, term_id, sections_csv, active, source "
            "FROM watches ORDER BY rowid"
        ).fetchall()
        return [
            WatchRow(
                r["course_id"],
                r["term_id"],
                self._sections_tuple(r["sections_csv"]),
                bool(r["active"]),
                r["source"],
            )
            for r in rows
        ]

    def watch_row(self, course_id: str, term_id: str) -> WatchRow | None:
        r = self.connection.execute(
            "SELECT course_id, term_id, sections_csv, active, source FROM watches "
            "WHERE course_id = ? AND term_id = ?",
            (course_id, term_id),
        ).fetchone()
        if r is None:
            return None
        return WatchRow(
            r["course_id"],
            r["term_id"],
            self._sections_tuple(r["sections_csv"]),
            bool(r["active"]),
            r["source"],
        )

    def add_or_replace_ui_watch(
        self, course_id: str, term_id: str, sections: tuple[str, ...]
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO watches "
                "(course_id, term_id, sections_csv, active, source) "
                "VALUES (?, ?, ?, 1, 'ui') "
                "ON CONFLICT(course_id, term_id) DO UPDATE SET "
                "sections_csv = excluded.sections_csv, active = 1, source = 'ui'",
                (course_id, term_id, ",".join(sections)),
            )

    def set_watch_sections(
        self, course_id: str, term_id: str, sections: tuple[str, ...]
    ) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE watches SET sections_csv = ?, source = 'ui' "
                "WHERE course_id = ? AND term_id = ?",
                (",".join(sections), course_id, term_id),
            )

    def set_watch_active(
        self, course_id: str, term_id: str, active: bool
    ) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE watches SET active = ?, source = 'ui' "
                "WHERE course_id = ? AND term_id = ?",
                (1 if active else 0, course_id, term_id),
            )

    def delete_watch(self, course_id: str, term_id: str) -> None:
        with self.connection:
            for table in ("watches", "watch_health", "section_snapshots"):
                self.connection.execute(
                    f"DELETE FROM {table} WHERE course_id = ? AND term_id = ?",
                    (course_id, term_id),
                )
```

- [ ] **Step 4: Run the db tests**

Run: `python -m pytest tests/test_db.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 6: Commit**

```bash
git add src/testudo_watch/db.py tests/test_db.py
git commit -m "feat(db): watch CRUD methods for UI editing"
```

---

## Task 3: web.py — read-write open, `create_app(config, file_watches)`, ManageView builder

**Files:**
- Modify: `src/testudo_watch/web.py`, `tests/test_web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Database`, `get_all_watches`, `WatchRow` (Task 2); `Watch` (`testudo_watch.models`).
- Produces:
  - `create_app(config: AppConfig, file_watches: list[Watch]) -> FastAPI`.
  - `_load_views` opens `Database(config.db_path)` (read-write) and also treats `testudo_watch.db.DatabaseError` as a guidance-page condition.
  - `ManageRow(course_id, term_id, section_label, source, in_file)` and `ManageView(active: list[ManageRow], disabled: list[ManageRow])`, both frozen.
  - `build_manage_view(db: Database, file_watches: list[Watch]) -> ManageView`.

- [ ] **Step 1: Write failing tests — new bits in `tests/test_web.py`**

At the top of `tests/test_web.py`, add `from testudo_watch.web import build_manage_view`. Then append:

```python
def test_build_manage_view_splits_active_and_disabled(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])           # file, active
    db.add_or_replace_ui_watch("MATH240", "202601", ())                # ui, active
    db.add_or_replace_ui_watch("PHYS161", "202601", ("0201",))
    db.set_watch_active("PHYS161", "202601", False)                    # ui, disabled
    view = build_manage_view(db, [Watch("CMSC351", "202601", ("0101",))])
    assert [(r.course_id, r.source, r.in_file) for r in view.active] == [
        ("CMSC351", "file", True),
        ("MATH240", "ui", False),
    ]
    assert [(r.course_id, r.in_file) for r in view.disabled] == [("PHYS161", False)]
    assert view.active[0].section_label == "0101"
    assert view.active[1].section_label == ""
    db.close()
```

Also update the six existing `create_app(cfg(...))` call sites to pass `, []`
(e.g. `create_app(cfg(tmp_path / "s.db"), [])`). Then **delete** the two Phase 2
tests that assumed a non-migrating read-only open —
`test_missing_database_shows_guidance_not_500` and
`test_pre_v2_database_shows_guidance` — and add these two in their place:

```python
def test_missing_database_is_created_and_renders_empty_dashboard(tmp_path):
    # read-write serve creates + migrates the DB on first request, like `run`
    missing = tmp_path / "created.db"
    client = TestClient(create_app(cfg(missing), []))
    r = client.get("/")
    assert r.status_code == 200
    assert "testudo-watch run" in r.text  # "not completed a poll yet" guidance
    assert missing.exists()


def test_newer_schema_database_shows_guidance(tmp_path):
    p = tmp_path / "v99.db"
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    client = TestClient(create_app(cfg(str(p)), []))
    r = client.get("/")
    assert r.status_code == 200 and "testudo-watch" in r.text  # guidance page, not 500
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_web.py -q`
Expected: `ImportError` on `build_manage_view` / `ManageRow`; `TypeError` on `create_app()` missing `file_watches`.

- [ ] **Step 3: Update `web.py`**

Change the db import line to also bring in the schema-error class under a clear alias, and add `get_all_watches` is a method so no import needed:

```python
from testudo_watch.db import Database, DatabaseError as SchemaError
```

Add the manage dataclasses near the other view dataclasses:

```python
@dataclass(frozen=True)
class ManageRow:
    course_id: str
    term_id: str
    section_label: str
    source: str
    in_file: bool


@dataclass(frozen=True)
class ManageView:
    active: list[ManageRow]
    disabled: list[ManageRow]
```

Add the builder near the other `build_*_view` functions:

```python
def build_manage_view(db: Database, file_watches) -> ManageView:
    in_file = {(w.course_id, w.term_id) for w in file_watches}
    active: list[ManageRow] = []
    disabled: list[ManageRow] = []
    for w in db.get_all_watches():
        row = ManageRow(
            course_id=w.course_id,
            term_id=w.term_id,
            section_label=", ".join(w.sections),
            source=w.source,
            in_file=(w.course_id, w.term_id) in in_file,
        )
        (active if w.active else disabled).append(row)
    return ManageView(active=active, disabled=disabled)
```

Change `create_app` and `_load_views`:

```python
def create_app(config: AppConfig, file_watches) -> FastAPI:
    app = FastAPI(title="testudo-watch")

    def _load_views() -> dict | None:
        now = datetime.now(timezone.utc)
        try:
            db = Database(config.db_path)
        except (sqlite3.DatabaseError, SchemaError) as exc:
            _log.warning("dashboard could not open %s: %s", config.db_path, exc)
            return None
        try:
            return {
                "status": build_status_view(db, config, now=now),
                "watches": build_watches_view(db, now=now),
                "notifications": build_notifications_view(db, now=now),
            }
        except sqlite3.DatabaseError as exc:
            _log.warning("dashboard could not read %s: %s", config.db_path, exc)
            return None
        finally:
            db.close()
    # ... existing GET / and GET /fragments/* routes unchanged ...
    return app
```

- [ ] **Step 4: Run `tests/test_web.py`**

Run: `python -m pytest tests/test_web.py -q`
Expected: all pass (the reworked missing-DB / newer-schema tests, `build_manage_view`, and the six call-site-updated tests).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: `tests/test_cli.py`'s `serve` test still passes (it monkeypatches `create_app`, so the new required arg does not reach the real function yet — but `main` calls `_cmd_serve` which calls `create_app(config)` with one arg → this WILL fail). If `test_serve_invokes_uvicorn_with_parsed_host_and_port` fails here, that is expected and fixed in Task 7; note it and proceed. All other tests green, 0 warnings.

Actually to keep the suite green between tasks, also do the tiny Task 7 CLI change now is NOT allowed (separate task). Instead, in this task, make `file_watches` default to `None` and treat `None` as `[]` inside `create_app` / `build_manage_view` (`file_watches = file_watches or []`). Then `create_app(config)` still works and the CLI test stays green until Task 7 makes the call explicit. Update the `create_app` signature to `create_app(config: AppConfig, file_watches=None) -> FastAPI` and add `file_watches = list(file_watches or [])` as the first line.

- [ ] **Step 6: Adjust for the default and re-run**

Set `create_app(config: AppConfig, file_watches=None) -> FastAPI`, first line `file_watches = list(file_watches or [])`. Re-run `python -m pytest -q` — expected fully green, 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add src/testudo_watch/web.py tests/test_web.py
git commit -m "feat(web): read-write DB open, create_app(file_watches), ManageView builder"
```

---

## Task 4: web.py — `GET /watches` page + templates + dashboard nav

**Files:**
- Modify: `src/testudo_watch/web.py`, `src/testudo_watch/templates/dashboard.html`
- Create: `src/testudo_watch/templates/manage.html`, `src/testudo_watch/templates/_manage.html`
- Test: `tests/test_web_manage.py`

**Interfaces:**
- Consumes: `build_manage_view` (Task 3), `_TEMPLATES` (existing).
- Produces: `GET /watches` → `manage.html` (HTTP 200). `_manage.html` renders the add form + an "Active" table + a "Disabled" table; every mutation route (Tasks 5-6) returns `_manage.html`.

- [ ] **Step 1: Write failing tests — `tests/test_web_manage.py`**

```python
import sqlite3

from fastapi.testclient import TestClient

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import Watch
from testudo_watch.web import create_app


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30, notifier="console",
        db_path=str(db_path), log_dir="logs",
    )


def client(tmp_path, file_watches=None):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.add_or_replace_ui_watch("MATH240", "202601", ())
    db.add_or_replace_ui_watch("PHYS161", "202601", ("0201",))
    db.set_watch_active("PHYS161", "202601", False)
    db.close()
    return TestClient(create_app(cfg(tmp_path / "s.db"), file_watches or []))


def test_watches_page_lists_active_and_disabled(tmp_path):
    r = client(tmp_path).get("/watches")
    assert r.status_code == 200
    assert "CMSC351" in r.text and "MATH240" in r.text
    assert "PHYS161" in r.text          # disabled section
    assert 'name="course_id"' in r.text  # add form present
    assert 'name="term_id"' in r.text
    assert 'name="sections"' in r.text


def test_dashboard_links_to_manage(tmp_path):
    db = Database(tmp_path / "s.db")
    db.close()
    r = TestClient(create_app(cfg(tmp_path / "s.db"), [])).get("/")
    assert '/watches' in r.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_web_manage.py -q`
Expected: 404 on `/watches`; `/watches` string absent from `/`.

- [ ] **Step 3: Create `src/testudo_watch/templates/_manage.html`**

```html
{% if error %}<p class="banner">{{ error }}</p>{% endif %}
{% if notice %}<p class="banner">{{ notice }}</p>{% endif %}

<form hx-post="/watches" hx-target="#manage" hx-swap="innerHTML" style="margin:.75rem 0">
  <input name="course_id" placeholder="CMSC351" size="10" required>
  <input name="term_id" placeholder="202601" size="8" required>
  <input name="sections" placeholder="0101, 0201 (blank = any)" size="22">
  <button type="submit">Add watch</button>
</form>

<h3>Active</h3>
{% if not view.active %}<p class="muted">No active watches.</p>{% endif %}
{% for r in view.active %}
<div style="border-bottom:1px solid #eee;padding:.3rem 0">
  <strong>{{ r.course_id }}</strong> <span class="muted">{{ r.term_id }} · {{ r.source }}</span>
  <form hx-post="/watches/{{ r.course_id }}/{{ r.term_id }}/sections"
        hx-target="#manage" hx-swap="innerHTML" style="display:inline">
    <input name="sections" value="{{ r.section_label }}" placeholder="any section" size="18">
    <button type="submit">Save sections</button>
  </form>
  <button hx-post="/watches/{{ r.course_id }}/{{ r.term_id }}/active"
          hx-vals='{"active": "0"}' hx-target="#manage" hx-swap="innerHTML">Disable</button>
  <button hx-post="/watches/{{ r.course_id }}/{{ r.term_id }}/delete"
          hx-target="#manage" hx-swap="innerHTML">Delete</button>
</div>
{% endfor %}

<h3>Disabled</h3>
{% if not view.disabled %}<p class="muted">None.</p>{% endif %}
{% for r in view.disabled %}
<div style="border-bottom:1px solid #eee;padding:.3rem 0">
  <strong>{{ r.course_id }}</strong> <span class="muted">{{ r.term_id }}{% if r.in_file %} · in watches.toml{% endif %}</span>
  <button hx-post="/watches/{{ r.course_id }}/{{ r.term_id }}/active"
          hx-vals='{"active": "1"}' hx-target="#manage" hx-swap="innerHTML">Enable</button>
  <button hx-post="/watches/{{ r.course_id }}/{{ r.term_id }}/delete"
          hx-target="#manage" hx-swap="innerHTML">Delete</button>
</div>
{% endfor %}
```

- [ ] **Step 4: Create `src/testudo_watch/templates/manage.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>testudo-watch — manage watches</title>
<script src="https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js"></script>
<style>
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 2rem auto; padding: 0 1rem; }
  .banner { background: #fdf0d5; border: 1px solid #e0b84c; padding: .5rem .75rem; border-radius: 4px; margin: .4rem 0; }
  .muted { color: #666; font-size: .85em; }
  nav a { margin-right: 1rem; }
  button { margin-left: .3rem; }
</style>
</head>
<body>
<h1>testudo-watch</h1>
<nav><a href="/">Dashboard</a><a href="/watches">Manage watches</a></nav>
<h2>Watches</h2>
<div id="manage">
  {% include "_manage.html" %}
</div>
</body>
</html>
```

- [ ] **Step 5: Add the nav line to `dashboard.html`**

In `src/testudo_watch/templates/dashboard.html`, immediately after `<h1>testudo-watch</h1>`, add:

```html
<nav style="margin-bottom:.5rem"><a href="/">Dashboard</a> · <a href="/watches">Manage watches</a></nav>
```

- [ ] **Step 6: Add the `GET /watches` route in `web.py`**

Inside `create_app`, before `return app`:

```python
    @app.get("/watches", response_class=HTMLResponse)
    def watches_page(request: Request):
        db = Database(config.db_path)
        try:
            view = build_manage_view(db, file_watches)
        finally:
            db.close()
        return _TEMPLATES.TemplateResponse(
            request, "manage.html", {"view": view, "error": None, "notice": None}
        )
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_web_manage.py tests/test_web.py -q`
Expected: pass. Then `python -m pytest -q` — green, 0 warnings.

- [ ] **Step 8: Commit**

```bash
git add src/testudo_watch/web.py src/testudo_watch/templates/manage.html src/testudo_watch/templates/_manage.html src/testudo_watch/templates/dashboard.html tests/test_web_manage.py
git commit -m "feat(web): GET /watches page + manage templates + dashboard nav"
```

---

## Task 5: web.py — add + edit-sections routes (with Testudo probe)

**Files:**
- Modify: `src/testudo_watch/web.py`
- Test: `tests/test_web_manage.py`

**Interfaces:**
- Consumes: `add_or_replace_ui_watch`, `set_watch_sections`, `watch_row` (Task 2); `build_manage_view` (Task 3).
- Produces:
  - `POST /watches` (form: `course_id`, `term_id`, `sections`) → validate + probe → `add_or_replace_ui_watch` → `_manage.html` (200) or `_manage.html` + `error` (422).
  - `POST /watches/{course_id}/{term_id}/sections` (form: `sections`) → 404 if the row is absent → validate the list against a fresh probe → `set_watch_sections` → `_manage.html`.
  - Module-level helpers in `web.py`: `_COURSE_RE`, `_TERM_RE`, `_parse_sections(raw: str) -> tuple[str, ...]`, `_probe(course_id, term_id) -> list[str]` (returns section ids; raises `ProbeError(str)` on any failure).

- [ ] **Step 1: Write failing tests — append to `tests/test_web_manage.py`**

```python
import pytest

from testudo_watch import web


@pytest.fixture
def fake_probe(monkeypatch):
    calls = {}

    def _set(section_ids, *, raises=None):
        def probe(course_id, term_id):
            calls["args"] = (course_id, term_id)
            if raises:
                raise web.ProbeError(raises)
            return list(section_ids)

        monkeypatch.setattr(web, "_probe", probe)

    _set.calls = calls
    return _set


def test_add_watch_happy_path(tmp_path, fake_probe):
    fake_probe(["0101", "0201", "0301"])
    c = client(tmp_path)
    r = c.post("/watches", data={"course_id": "cmsc330", "term_id": "202601",
                                 "sections": "0101, 0201"})
    assert r.status_code == 200
    assert "CMSC330" in r.text
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CMSC330", "202601").source == "ui"
    db.close()


def test_add_watch_rejects_malformed_course(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches", data={"course_id": "cs", "term_id": "202601",
                                                "sections": ""})
    assert r.status_code == 422
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CS", "202601") is None
    db.close()


def test_add_watch_rejects_when_probe_fails(tmp_path, fake_probe):
    fake_probe([], raises="Testudo returned no sections for CMSC999 in 202601")
    r = client(tmp_path).post("/watches", data={"course_id": "CMSC999",
                                                "term_id": "202601", "sections": ""})
    assert r.status_code == 422
    assert "no sections" in r.text
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CMSC999", "202601") is None
    db.close()


def test_add_watch_rejects_unknown_section(tmp_path, fake_probe):
    fake_probe(["0101", "0201"])
    r = client(tmp_path).post("/watches", data={"course_id": "CMSC330",
                                                "term_id": "202601",
                                                "sections": "0101, 9999"})
    assert r.status_code == 422
    assert "9999" in r.text
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CMSC330", "202601") is None
    db.close()


def test_edit_sections_happy_path(tmp_path, fake_probe):
    fake_probe(["0101", "0202"])
    r = client(tmp_path).post("/watches/CMSC351/202601/sections",
                              data={"sections": "0101, 0202"})
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    row = db.watch_row("CMSC351", "202601")
    assert row.sections == ("0101", "0202") and row.source == "ui"
    db.close()


def test_edit_sections_404_when_absent(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/NOPE/202601/sections",
                              data={"sections": "0101"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_web_manage.py -q -k "add_watch or edit_sections"`
Expected: `AttributeError: module 'testudo_watch.web' has no attribute 'ProbeError'` / 404 on `POST /watches`.

- [ ] **Step 3: Add the helpers + routes to `web.py`**

Add imports:

```python
import re

import requests

from testudo_watch.scraper import ScrapeError, build_session, fetch_sections
```

Add module-level helpers (near `STALE_GRACE_SECONDS`):

```python
_COURSE_RE = re.compile(r"^[A-Z]{4}\d{3}[A-Z]?$")
_TERM_RE = re.compile(r"^\d{6}$")


class ProbeError(Exception):
    """A watch could not be validated against Testudo."""


def _parse_sections(raw: str) -> tuple[str, ...]:
    return tuple(s for s in re.split(r"[,\s]+", raw.strip()) if s)


def _probe(course_id: str, term_id: str) -> list[str]:
    session = build_session()
    try:
        snaps = fetch_sections(session, course_id, term_id)
    except (ScrapeError, requests.RequestException) as exc:
        raise ProbeError(
            f"Testudo returned no sections for {course_id} in {term_id} "
            f"— check the course id and term ({exc})"
        ) from exc
    finally:
        session.close()
    return [s.section_id for s in snaps]


def _validate_and_probe(course_id: str, term_id: str, sections: tuple[str, ...]):
    """Returns normalized (course_id, sections). Raises ProbeError on any failure."""
    course_id = course_id.strip().upper()
    term_id = term_id.strip()
    if not _COURSE_RE.match(course_id):
        raise ProbeError(f"course id {course_id!r} looks wrong (expected e.g. CMSC351)")
    if not _TERM_RE.match(term_id):
        raise ProbeError(f"term id {term_id!r} must be 6 digits (e.g. 202601)")
    available = _probe(course_id, term_id)
    missing = [s for s in sections if s not in available]
    if missing:
        raise ProbeError(
            f"section {', '.join(missing)} not found. Available: {', '.join(sorted(available))}"
        )
    return course_id, term_id, sections
```

Add the routes inside `create_app` (before `return app`):

```python
    def _manage_response(request: Request, db, *, error=None, notice=None, status=200):
        view = build_manage_view(db, file_watches)
        return _TEMPLATES.TemplateResponse(
            request, "_manage.html",
            {"view": view, "error": error, "notice": notice},
            status_code=status,
        )

    @app.post("/watches", response_class=HTMLResponse)
    async def add_watch(request: Request):
        form = await request.form()
        db = Database(config.db_path)
        try:
            try:
                course_id, term_id, sections = _validate_and_probe(
                    form.get("course_id", ""),
                    form.get("term_id", ""),
                    _parse_sections(form.get("sections", "")),
                )
            except ProbeError as exc:
                return _manage_response(request, db, error=str(exc), status=422)
            db.add_or_replace_ui_watch(course_id, term_id, sections)
            return _manage_response(request, db, notice=f"Watching {course_id} {term_id}.")
        finally:
            db.close()

    @app.post("/watches/{course_id}/{term_id}/sections", response_class=HTMLResponse)
    async def edit_sections(request: Request, course_id: str, term_id: str):
        form = await request.form()
        db = Database(config.db_path)
        try:
            if db.watch_row(course_id, term_id) is None:
                return _manage_response(request, db, error="No such watch.", status=404)
            try:
                course_id, term_id, sections = _validate_and_probe(
                    course_id, term_id, _parse_sections(form.get("sections", ""))
                )
            except ProbeError as exc:
                return _manage_response(request, db, error=str(exc), status=422)
            db.set_watch_sections(course_id, term_id, sections)
            return _manage_response(request, db, notice="Sections updated.")
        finally:
            db.close()
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_web_manage.py -q`
Expected: the add / edit tests pass (the earlier Task 4 tests still pass). `python -m pytest -q` — green, 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/web.py tests/test_web_manage.py
git commit -m "feat(web): POST /watches add + edit-sections routes with Testudo probe"
```

---

## Task 6: web.py — enable/disable + delete routes

**Files:**
- Modify: `src/testudo_watch/web.py`
- Test: `tests/test_web_manage.py`

**Interfaces:**
- Consumes: `set_watch_active`, `delete_watch`, `watch_row` (Task 2); `_manage_response` (Task 5).
- Produces:
  - `POST /watches/{course_id}/{term_id}/active` (form: `active` = `"1"`/`"0"`) → 404 if absent → `set_watch_active` → `_manage.html`.
  - `POST /watches/{course_id}/{term_id}/delete` → 404 if absent → tombstone (`set_watch_active(..., False)`) + notice if `(course_id, term_id)` in `file_watches`, else `delete_watch` → `_manage.html`.

- [ ] **Step 1: Write failing tests — append to `tests/test_web_manage.py`**

```python
def test_toggle_active_off_then_on(tmp_path, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path)
    r = c.post("/watches/MATH240/202601/active", data={"active": "0"})
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    assert db.watch_row("MATH240", "202601").active is False
    db.close()
    c.post("/watches/MATH240/202601/active", data={"active": "1"})
    db = Database(tmp_path / "s.db")
    row = db.watch_row("MATH240", "202601")
    assert row.active is True and row.source == "ui"
    db.close()


def test_toggle_404_when_absent(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/NOPE/202601/active", data={"active": "0"})
    assert r.status_code == 404


def test_delete_ui_only_watch_hard_deletes(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/MATH240/202601/delete")  # ui-added, not in file
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    assert db.watch_row("MATH240", "202601") is None
    db.close()


def test_delete_file_backed_watch_tombstones(tmp_path, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path, file_watches=[Watch("CMSC351", "202601", ("0101",))])
    r = c.post("/watches/CMSC351/202601/delete")
    assert r.status_code == 200
    assert "watches.toml" in r.text  # explains why it wasn't removed
    db = Database(tmp_path / "s.db")
    row = db.watch_row("CMSC351", "202601")
    assert row is not None and row.active is False
    db.close()


def test_delete_404_when_absent(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/NOPE/202601/delete")
    assert r.status_code == 404


def test_get_routes_do_not_write(tmp_path, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path)
    db = Database(tmp_path / "s.db")
    before = {
        t: db.connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("watches", "section_snapshots", "notifications", "watch_health")
    }
    uv_before = db.connection.execute("PRAGMA user_version").fetchone()[0]
    db.close()
    for _ in range(3):
        c.get("/")
        c.get("/watches")
        c.get("/fragments/watches")
    db = Database(tmp_path / "s.db")
    after = {
        t: db.connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("watches", "section_snapshots", "notifications", "watch_health")
    }
    assert after == before
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == uv_before
    db.close()


def test_ui_watch_survives_a_sync(tmp_path, fake_probe):
    fake_probe(["0101", "0201"])
    c = client(tmp_path)
    c.post("/watches", data={"course_id": "CMSC330", "term_id": "202601",
                             "sections": "0101"})
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])  # run's startup sync
    row = db.watch_row("CMSC330", "202601")
    assert row is not None and row.active is True and row.source == "ui"
    db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_web_manage.py -q -k "toggle or delete or get_routes_do_not_write or survives_a_sync"`
Expected: 404s on the new routes.

- [ ] **Step 3: Add the routes to `web.py`**

Inside `create_app`, before `return app`:

```python
    @app.post("/watches/{course_id}/{term_id}/active", response_class=HTMLResponse)
    async def toggle_active(request: Request, course_id: str, term_id: str):
        form = await request.form()
        db = Database(config.db_path)
        try:
            if db.watch_row(course_id, term_id) is None:
                return _manage_response(request, db, error="No such watch.", status=404)
            db.set_watch_active(course_id, term_id, form.get("active") == "1")
            return _manage_response(request, db)
        finally:
            db.close()

    @app.post("/watches/{course_id}/{term_id}/delete", response_class=HTMLResponse)
    async def delete_watch_route(request: Request, course_id: str, term_id: str):
        db = Database(config.db_path)
        try:
            if db.watch_row(course_id, term_id) is None:
                return _manage_response(request, db, error="No such watch.", status=404)
            if (course_id, term_id) in {(w.course_id, w.term_id) for w in file_watches}:
                db.set_watch_active(course_id, term_id, False)
                return _manage_response(
                    request, db,
                    notice=f"{course_id} is still in watches.toml — disabled, not "
                    f"deleted. Remove it from the file to delete it permanently.",
                )
            db.delete_watch(course_id, term_id)
            return _manage_response(request, db, notice=f"Deleted {course_id} {term_id}.")
        finally:
            db.close()
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_web_manage.py -q`
Expected: all pass. `python -m pytest -q` — green except possibly `tests/test_cli.py::test_serve_invokes_uvicorn_with_parsed_host_and_port` (fixed in Task 7); note it.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/web.py tests/test_web_manage.py
git commit -m "feat(web): enable/disable + delete watch routes (file-guarded)"
```

---

## Task 7: CLI wiring + README + full-suite green

**Files:**
- Modify: `src/testudo_watch/cli.py`, `tests/test_cli.py`, `README.md`

**Interfaces:**
- Consumes: `create_app(config, file_watches)` (Task 3).
- Produces: `_cmd_serve(config, watches, *, host: str, port: int) -> int` → `uvicorn.run(create_app(config, watches), host=host, port=port)`; `main` passes the parsed `watches`.

- [ ] **Step 1: Update the failing test — `tests/test_cli.py`**

In `test_serve_invokes_uvicorn_with_parsed_host_and_port`, change `fake_create_app` to accept the second argument and assert it:

```python
    def fake_create_app(config, file_watches):
        seen["config"] = config
        seen["file_watches"] = file_watches
        return object()
```

and after the existing asserts add (this file's `write_config` emits one
`[[watch]]` block for `CMSC351 / 202601`):

```python
    assert [w.course_id for w in seen["file_watches"]] == ["CMSC351"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_cli.py -q -k serve`
Expected: `TypeError: _cmd_serve() ... ` / `create_app()` arg mismatch, or the new `file_watches` assert fails.

- [ ] **Step 3: Update `cli.py`**

Change `_cmd_serve` and its call in `main`:

```python
def _cmd_serve(config, watches, *, host: str, port: int) -> int:
    import uvicorn

    from testudo_watch.web import create_app

    uvicorn.run(create_app(config, watches), host=host, port=port)
    return 0
```

In `main`, the `serve` branch call becomes:

```python
        try:
            return _cmd_serve(config, watches, host=args.host, port=args.port)
```

(`watches` is already in scope from `config, watches = _load(args)`.)

- [ ] **Step 4: Run the CLI tests**

Run: `python -m pytest tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Tighten `create_app`'s signature (remove the Task 3 default)**

Now that every caller passes `file_watches`, change `web.py`'s signature back to required: `def create_app(config: AppConfig, file_watches) -> FastAPI:` and drop the `file_watches = list(file_watches or [])` line's `or []` — keep `file_watches = list(file_watches)` for a defensive copy. Re-run `python -m pytest -q`.

Expected: fully green, 0 warnings. If any `create_app(...)` call site lacks the argument, fix it (all should have been updated in Tasks 3/4/7).

- [ ] **Step 6: Document the `/watches` page in `README.md`**

Under the "Web dashboard" section, after the existing paragraph, add:

```markdown
### Managing watches from the browser

`http://127.0.0.1:8477/watches` lists every watch and lets you add, edit the
section list, enable/disable, or delete one. Adds and edits are checked against
Testudo live — a bad course id or term is rejected on the spot. Changes take
effect on the watcher's next poll with no restart.

Watches you edit here are marked UI-owned and are **not** overwritten or removed
when `testudo-watch run` re-reads `watches.toml` on startup. Deleting a watch
that is still listed in `watches.toml` disables it instead (remove it from the
file to delete it for good).
```

- [ ] **Step 7: Full suite + manual smoke note**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

Manual (network, two terminals — for the human, not the implementer):
1. `testudo-watch run` in one terminal.
2. `testudo-watch serve` in another; open `http://127.0.0.1:8477/watches`.
3. Add a real current course; confirm it validates, appears in the list and on the dashboard within one poll cycle, with no `run` restart.
4. Add a bogus course id → inline rejection, nothing added.
5. Edit its sections, disable, re-enable, delete.
6. Restart `testudo-watch run`; confirm the UI-added watch is still there.

- [ ] **Step 8: Commit**

```bash
git add src/testudo_watch/cli.py src/testudo_watch/web.py tests/test_cli.py README.md
git commit -m "feat(cli): wire file watches into serve; document the /watches page"
```

---

## Self-Review Notes

**Spec coverage:**
- `source` column, `SCHEMA_VERSION` 3, `PRAGMA table_info`-guarded `ALTER` migration → Task 1.
- `sync_watches` respects `source` (skip `ui` on update + deactivate) → Task 1.
- WAL + `busy_timeout` on write connections, `busy_timeout` on read-only → Task 1.
- `WatchRow`, `get_all_watches`, `watch_row`, `add_or_replace_ui_watch`, `set_watch_sections`, `set_watch_active`, `delete_watch` (+ related-row cleanup) → Task 2.
- `serve` read-write open; `create_app(config, file_watches)`; guidance page also on `DatabaseError` → Task 3.
- `ManageRow` / `ManageView` / `build_manage_view` (active vs disabled, `in_file`, `section_label`) → Task 3.
- `GET /watches` + `manage.html` / `_manage.html` + dashboard nav → Task 4.
- `POST /watches` add with regex + probe validation (422 + inline error, no write) → Task 5.
- `POST …/sections` edit with 404 + probe validation → Task 5.
- `POST …/active` toggle with 404 → Task 6.
- `POST …/delete` with 404, file-guard (tombstone vs hard delete) → Task 6.
- Read routes issue no writes (regression test) → Task 6.
- End-to-end: UI watch survives `sync_watches` → Task 6.
- `_cmd_serve` passes `file_watches`; README `/watches` section → Task 7.
- `.gitignore` WAL sidecars → Task 1.

**Deviation from the spec, called out for the reader:** the spec says a
missing/pre-v2 DB renders the Phase 2 guidance page. Because `serve` now opens
read-write it **creates and migrates** the DB on first request (like `run`), so
a missing file yields an empty dashboard ("run testudo-watch run") and a
pre-v3 file is silently upgraded to v3. The guidance page now appears only for a
corrupt/non-SQLite file or a newer-than-v3 file. Tests updated accordingly in
Task 3. A typo'd `--db` path therefore creates a stray DB rather than showing an
error — same behavior `run` already has.

**Placeholder scan:** none. Every step carries literal code, SQL, or template
HTML. The one conditional instruction (Task 7 Step 1: pick the assertion that
matches `write_config`) names both concrete alternatives.

**Type consistency:** `WatchRow` fields (`course_id, term_id, sections,
active, source`) are used identically in Task 2's methods and Task 3's
`build_manage_view`. `ManageRow` fields (`course_id, term_id, section_label,
source, in_file`) match between Task 3's builder and Task 4's `_manage.html`.
`create_app(config, file_watches)` matches across Tasks 3, 4, 5, 6, 7 and the
`test_web.py` / `test_web_manage.py` / `test_cli.py` call sites. `_probe`,
`ProbeError`, `_validate_and_probe`, `_parse_sections`, `_manage_response` are
defined in Task 5 and reused in Task 6. `set_watch_active(course, term, bool)`
is used by both the toggle route and the delete-tombstone path.
