# Testudo Watch — Phase 3a Design (Edit Watches from the UI)

Date: 2026-09-10
Status: Approved design, pending spec review

## Context

Phases 1 & 2 are merged. `testudo-watch run` polls Testudo and writes SQLite
(`watches`, `section_snapshots`, `notifications`, `watcher_heartbeat`,
`watch_health`); `testudo-watch serve` is a **read-only** FastAPI dashboard
(`mode=ro`) rendering that data via Jinja2 + HTMX. Watches are defined in
`watches.toml`; `run` calls `db.sync_watches(file_watches)` on every startup,
which upserts the file's `[[watch]]` blocks as `active=1` and deactivates any
DB watch absent from the file.

Phase 3 as roadmapped bundles three independent features (UI watch editing;
notification-history views; more notifier channels). This spec is **Phase 3a —
UI watch editing only**. The other two are separate later specs.

## Goal

A `/watches` page in the web UI where the user can **add**, **edit the section
list of**, **enable/disable**, and **delete** watches. Watches edited in the UI
are stored in SQLite and survive `testudo-watch run` restarts even though
`watches.toml` auto-sync stays on.

Out of scope for 3a: notification history/filtering; new notifier channels;
editing app settings (`poll_interval_seconds`, `notifier`) from the UI;
authentication; a parity `testudo-watch watch …` CLI.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Scope | Watch editing only (Phase 3a). |
| `watches.toml` role | **Keep auto-sync.** A per-watch `source` column (`'file'` \| `'ui'`) protects UI edits: startup sync never overwrites or deactivates a `source='ui'` row. |
| Conflict rule | Any mutating UI action sets the row's `source='ui'`. Once `'ui'`, `sync_watches` skips it entirely. |
| `serve` write access | `serve` opens the DB **read-write**. Read routes still only SELECT (enforced by tests, not a `mode=ro` lock). Enable `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` so `run` and `serve` writes coexist. |
| Add validation | Format-check `course_id` / `term_id`, then one live `fetch_sections` probe; reject if Testudo returns no sections, or if a named section isn't in the probe result. |
| UI layout | A separate **`/watches` page** (not inline on the dashboard, not a drawer). Header links cross-navigate. |
| Edit responses | HTMX: each mutation POSTs and gets back the refreshed watches-table fragment. |
| Delete | Hard-delete when the course/term is **not** in the current `watches.toml`; otherwise tombstone (`active=0, source='ui'`) and tell the user to remove it from the file. |

## Roadmap (context; not built here)

- Phase 3b: notification history view with filtering (channel, status, date, course).
- Phase 3c: additional notifier channels (email, push, …), UI channel selection.

## Architecture

### Changed / new files

```
.gitignore                              # add *.db-wal / *.db-shm (WAL sidecars)
src/testudo_watch/db.py                 # SCHEMA_VERSION 2->3; `source` column + stepwise migration; WAL/busy_timeout; WatchRow; get_all_watches; add_or_replace_ui_watch; set_watch_sections; set_watch_active; delete_watch; watch_health/section_snapshots cleanup on hard delete; sync_watches respects `source`
src/testudo_watch/web.py               # create_app(config, file_watches); ManageView builder; GET /watches + 4 mutation routes; probe via scraper
src/testudo_watch/templates/
  manage.html                          # full /watches page
  _manage.html                         # watches-table + add-form fragment (returned by every mutation)
  dashboard.html                       # add a header link to /watches
src/testudo_watch/cli.py               # _cmd_serve passes the parsed file `watches` into create_app
tests/test_db.py                       # v2->v3 migration; `source` semantics in sync_watches; new methods; WAL pragma
tests/test_web_manage.py               # /watches page + add/edit/toggle/delete routes (TestClient)
tests/test_web.py                      # update create_app(...) call sites for the new signature
tests/test_cli.py                      # serve still wires create_app (now with file_watches)
README.md                              # document the /watches page
```

No file is deleted. `engine.py`, `web_time.py`, `scraper.py`, `notifier/`,
`config.py`, `models.py`, `diff.py` are unchanged.

### Data model

`watches` gains one column:

```sql
source TEXT NOT NULL DEFAULT 'file'    -- 'file' | 'ui'
```

`SCHEMA_VERSION` 2 → 3. `_SCHEMA`'s `CREATE TABLE IF NOT EXISTS watches`
includes `source` (so a fresh DB has it). `migrate()` gains real stepwise
logic, made idempotent by a column probe rather than trusting `found`:

```python
def migrate(self) -> None:
    found = self.connection.execute("PRAGMA user_version").fetchone()[0]
    if found > SCHEMA_VERSION:
        raise DatabaseError(... "schema v{found} > v{SCHEMA_VERSION}" ...)
    self.connection.executescript(_SCHEMA)          # create any missing tables
    cols = {r[1] for r in self.connection.execute("PRAGMA table_info(watches)")}
    if "source" not in cols:                        # pre-v3 `watches` already existed
        self.connection.execute(
            "ALTER TABLE watches ADD COLUMN source TEXT NOT NULL DEFAULT 'file'"
        )
    self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    self.connection.commit()
```

A v1 or v2 file upgrades transparently (existing `watches` rows get
`source='file'`); a fresh DB gets `source` from the CREATE; a v>3 file still
raises `DatabaseError`.

### Connection settings (`Database.__init__`)

Write-mode branch, right after `sqlite3.connect(...)`:

```python
self.connection.execute("PRAGMA journal_mode = WAL")
self.connection.execute("PRAGMA busy_timeout = 5000")
```

`journal_mode=WAL` is persisted in the DB header (set once, harmless to
re-issue); `busy_timeout` is per-connection. Both `run` and `serve` therefore
get WAL + a 5 s busy wait, so the poll loop's writes and the UI's edits no
longer collide with `database is locked`. The `read_only=True` branch sets only
`busy_timeout` (a `mode=ro` connection cannot change `journal_mode`); it is no
longer used by `serve` but stays for the existing `db` tests. WAL creates
`<db>-wal` / `<db>-shm` sidecars → add `*.db-wal`, `*.db-shm` to `.gitignore`.

### New `Database` API

```python
@dataclass(frozen=True)
class WatchRow:
    course_id: str
    term_id: str
    sections: tuple[str, ...]
    active: bool
    source: str            # 'file' | 'ui'
```

- `get_all_watches() -> list[WatchRow]` — active **and** inactive, `ORDER BY rowid`.
- `add_or_replace_ui_watch(course_id, term_id, sections: tuple[str, ...]) -> None`
  — `INSERT … (…, active, source) VALUES (…, 1, 'ui') ON CONFLICT(course_id,
  term_id) DO UPDATE SET sections_csv = excluded.sections_csv, active = 1,
  source = 'ui'`.
- `set_watch_sections(course_id, term_id, sections) -> None`
  — `UPDATE watches SET sections_csv = ?, source = 'ui' WHERE course_id = ? AND
  term_id = ?`.
- `set_watch_active(course_id, term_id, active: bool) -> None`
  — `UPDATE watches SET active = ?, source = 'ui' WHERE …`.
- `delete_watch(course_id, term_id) -> None` — `DELETE FROM watches …`, and also
  `DELETE FROM watch_health …` and `DELETE FROM section_snapshots …` for that
  course/term (no orphans). The route decides delete-vs-tombstone (below).
- `watch_row(course_id, term_id) -> WatchRow | None` — for existence checks / 404s.

`sync_watches(file_watches)` changes:
- upsert: `INSERT INTO watches (course_id, term_id, sections_csv, active,
  source) VALUES (?, ?, ?, 1, 'file') ON CONFLICT(course_id, term_id) DO UPDATE
  SET sections_csv = excluded.sections_csv, active = 1 WHERE watches.source =
  'file'`. A `source='ui'` conflict row is left untouched (the `WHERE` gates the
  UPDATE; the INSERT is already a conflict no-op).
- deactivate scan: `SELECT course_id, term_id FROM watches WHERE active = 1 AND
  source = 'file'` — only file-sourced rows are candidates for deactivation when
  absent from the file; `source='ui'` rows are never deactivated by sync.

`get_active_watches()` is unchanged (still `WHERE active = 1`), so `engine.run`
picks up UI adds on its next cycle with no restart.

### Web layer (`web.py`)

`create_app(config: AppConfig, file_watches: list[Watch]) -> FastAPI`
— `file_watches` is the parsed `[[watch]]` list from `watches.toml` (passed by
`_cmd_serve`), used only by the delete route's file-guard. A per-request
**read-write** `Database(config.db_path)` (no `read_only=True`); the existing
`_load_views` and read routes are unchanged apart from that open. The
`sqlite3.DatabaseError` → guidance-page handling stays.

New routes (all HTML responses):

| Route | Behavior |
|---|---|
| `GET /watches` | Full `manage.html`: header, an add form, and a table of all watches (active first, then a "Disabled" group), each row showing course/term/sections/source with edit + enable/disable + delete controls. |
| `POST /watches` | Add. Body: `course_id`, `term_id`, `sections` (comma/space list, optional). Validate + probe (§ below). On success `add_or_replace_ui_watch(...)`. Return `_manage.html` (200), or re-render it with an inline error (422). |
| `POST /watches/{course_id}/{term_id}/sections` | Edit sections. Validate the list against a fresh probe (same as add). `set_watch_sections(...)`. Return `_manage.html`. 404 if the row doesn't exist. |
| `POST /watches/{course_id}/{term_id}/active` | Body `active` = `"1"`/`"0"`. `set_watch_active(...)`. Return `_manage.html`. 404 if absent. |
| `POST /watches/{course_id}/{term_id}/delete` | 404 if the row doesn't exist. If `(course_id, term_id)` is in `file_watches` → `set_watch_active(..., active=False)` (tombstone) and flash "still in watches.toml — disabled, not deleted; remove it from the file to delete permanently". Else → `delete_watch(...)`. Return `_manage.html`. |

`ManageView` builder: `build_manage_view(db, file_watches) -> ManageView` with
`active: list[ManageRow]`, `disabled: list[ManageRow]`, where
`ManageRow(course_id, term_id, section_label, source, in_file: bool)`. Pure,
no `now` needed (no timestamps shown here).

The probe helper lives in `web.py`: `from testudo_watch.scraper import
build_session, fetch_sections, ScrapeError`. Add/edit build a short-lived
`Session`, call `fetch_sections`, and close it.

Header: `dashboard.html` and `manage.html` each get a one-line nav
(`Dashboard · Manage watches`) linking the two.

### Validation + probe

1. Normalize `course_id` to upper-case. Reject unless it matches
   `^[A-Z]{4}\d{3}[A-Z]?$`; reject `term_id` unless `^\d{6}$`. → 422 + inline
   field error, no DB write, no network call.
2. `sections` input is split on commas/whitespace into a tuple (empty → "any").
3. `fetch_sections(session, course_id, term_id)`:
   - `ScrapeError` → 422 + "Testudo returned no sections for {course} in
     {term} — check the course id and term."
   - If `sections` is non-empty, every id must appear in the probe result; else
     → 422 + "section {id} not found. Available: {sorted ids}."
4. Only then write the `watches` row. The web app **never** writes
   `section_snapshots` — a new watch shows "no sections seen yet" until
   `run`'s next cycle (≤ `poll_interval_seconds`).

### CLI

`_cmd_serve(config, watches, *, host, port)` — `main` already has the parsed
`watches`; pass them through to `create_app(config, watches)`. `serve`'s
argparse entry, exit-code handling, and `--host`/`--port` are unchanged.

### Error handling & concurrency

- WAL + `busy_timeout=5000` covers the two-writer case. A write that still times
  out → `sqlite3.OperationalError` → 503 + "database busy, try again", logged at
  WARNING.
- Malformed route params (path segments that don't match an existing row for
  edit/active/delete) → 404.
- Probe network failure other than `ScrapeError` (`requests.RequestException`)
  → treat as `ScrapeError`-equivalent: 422 + "could not reach Testudo to verify
  — try again".
- No CSRF token: `serve` binds `127.0.0.1`, single user, same-origin HTMX
  forms. Documented as a known limitation; revisit if `serve` ever binds a
  non-loopback host.

## Testing

- `tests/test_db.py`
  - v2→v3: build a DB at the current (v2) schema without `source`, reopen with
    the new `Database`; assert `watches` has a `source` column defaulting to
    `'file'` on existing rows and `user_version == 3`. Fresh DB: `source`
    present, `user_version == 3`. Seeding `user_version = 99` still raises
    `DatabaseError`.
  - `journal_mode` is `'wal'` after a write-mode open; `busy_timeout` is 5000.
  - `sync_watches`: a `source='ui'` row is neither updated nor deactivated by a
    sync that omits it; a `source='file'` row absent from the file is still
    deactivated; a new file watch inserts as `source='file'`.
  - `add_or_replace_ui_watch` sets `source='ui'`, `active=1`, and re-add
    replaces the section list; `set_watch_sections` / `set_watch_active` flip
    `source` to `'ui'`; `delete_watch` removes the row and its `watch_health` /
    `section_snapshots`.
  - `get_all_watches` returns inactive rows too, in `rowid` order.
- `tests/test_web_manage.py` (`TestClient`, temp DB, probe monkeypatched)
  - `GET /watches` renders active and disabled rows and the add form.
  - add happy path (probe returns `["0101","0201"]`) → row inserted `source='ui'`,
    fragment shows it.
  - add malformed course id → 422, no row.
  - add course the probe rejects (`ScrapeError`) → 422, no row.
  - add with a section not in the probe → 422 listing available ids.
  - edit sections; toggle active off then on; each returns `_manage.html` and
    persists with `source='ui'`.
  - delete a UI-only watch (not in `file_watches`) → row gone.
  - delete a watch that IS in `file_watches` → row tombstoned (`active=0`), still
    listed, message shown.
  - a read route (`GET /` or `/fragments/watches`) issues no writes — assert by
    opening a second `Database(..., read_only=True)` mid-test and confirming
    state is unchanged, or by wrapping the request connection to count writes.
  - after a UI add, `db.sync_watches([<only the original file watch>])` leaves
    the UI watch active (end-to-end of the `source` guarantee).
- `tests/test_web.py` — update the `create_app(cfg(...))` call sites to
  `create_app(cfg(...), [])`; existing assertions unchanged.
- `tests/test_cli.py` — the `serve` wiring test asserts `create_app` receives
  the config and the parsed `watches` list.
- Full suite stays at 0 warnings.

## Definition of done (Phase 3a)

- `testudo-watch serve` → `http://127.0.0.1:8477/watches` shows the add form and
  the watch table.
- Adding `CMSC351 / 202601 / 0101` (a real current course) validates against
  Testudo, inserts a `source='ui'` row, and the watch appears on the dashboard
  within one poll cycle of a running `testudo-watch run` — with **no `run`
  restart**.
- Adding a bogus course id is rejected inline with no DB write.
- Editing that watch's sections, disabling, re-enabling, and deleting all work
  from the UI and return the updated table without a full-page reload.
- Restarting `testudo-watch run` (which re-runs `sync_watches` from
  `watches.toml`) does **not** remove or overwrite the UI-added watch.
- `pytest` passes, 0 warnings.
- `README.md` documents the `/watches` page.
