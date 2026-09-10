# Testudo Watch — Phase 2 Design (Read-Only Web Dashboard)

Date: 2026-09-10
Status: Approved design, pending spec review

## Context

Phase 1 (merged) turned `send_sms.py` into the `testudo_watch` package: `config`
(`watches.toml`), `db` (SQLite), `scraper`, `diff`, `notifier/`, `engine`
(sync poll loop), `cli` (`testudo-watch run | check-once | list`). The engine's
`run()` is synchronous and blocks on `time.sleep` / `requests`; `Database` opens
one `sqlite3` connection; `SCHEMA_VERSION` is 1 and `migrate()` already raises
`DatabaseError` when the on-disk `user_version` is newer than the code.

Phase 2 adds a local web dashboard for **viewing** watcher state. It does not
change how detection or notification works.

## Goal

A `testudo-watch serve` command that runs a small FastAPI app on `127.0.0.1`,
reads the existing SQLite database, and renders an auto-refreshing dashboard:
configured watches with current per-section seat counts, recent notifications,
and watcher health (is it alive, is it failing).

Out of scope for Phase 2: editing watches from the UI, starting/stopping the
watcher from the UI, authentication, remote access, any notifier work, a JSON
API. Those are Phase 3 or later.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| UI capability | Read-only dashboard. No controls. Watcher is started separately (`testudo-watch run`). |
| Process model | **Two processes.** `run` is unchanged; `serve` is a separate pure DB reader. No shared memory, no threads, engine stays synchronous. |
| UI tech | Server-rendered Jinja2 templates + HTMX (one CDN `<script>`, no build step) polling fragment endpoints. |
| Health signal | **Both:** the engine writes an explicit heartbeat + per-watch health to the DB each cycle, and the dashboard also shows per-section snapshot freshness. |
| Bind / auth | `127.0.0.1` only, no auth (single-user local tool). |

## Roadmap (context; not built in Phase 2)

- Phase 3: add/edit/remove watches from the UI (SQLite becomes the source of
  truth for watches; `watches.toml` becomes an optional import); notification
  history views with filtering; additional notifier channels.

## Architecture

### New / changed files

```
pyproject.toml                         # + fastapi, uvicorn[standard], jinja2 (runtime); + httpx (dev); package-data for templates
src/testudo_watch/db.py                # SCHEMA_VERSION 1 -> 2; two new tables; read-only open mode; new read/write methods
src/testudo_watch/engine.py            # additive: write heartbeat + watch_health each cycle
src/testudo_watch/web.py               # create_app(config) factory, routes, view-model builders
src/testudo_watch/web_time.py          # humanize_age + UTC parse helpers (tiny, pure, unit-tested)
src/testudo_watch/templates/
  dashboard.html                       # full page: header + 3 fragment containers + HTMX script
  _status.html                         # watcher health fragment
  _watches.html                        # watches + per-section seat counts fragment
  _notifications.html                  # recent notifications fragment
src/testudo_watch/cli.py               # + `serve` subcommand
tests/test_db.py                       # + heartbeat / watch_health / v2 migration cases
tests/test_engine.py                   # + assert heartbeat & watch_health written
tests/test_web.py                      # TestClient: pages, fragments, stale banner, empty state
tests/test_web_time.py                 # humanize_age + staleness math
tests/test_cli.py                      # + `serve` wiring (uvicorn.run / create_app monkeypatched)
README.md                              # + "Web dashboard" section
```

No file is deleted. `run` / `check-once` / `list` behavior is unchanged.

### Dependencies

Runtime adds: `fastapi`, `uvicorn[standard]`, `jinja2`.
Dev adds: `httpx` (required by `starlette.testclient.TestClient`).
HTMX is loaded from a CDN `<script>` in `dashboard.html` — not a dependency.

### Schema changes (`db.py`)

`SCHEMA_VERSION` becomes `2`. Two tables are appended to `_SCHEMA` (all
`CREATE TABLE IF NOT EXISTS`, so `migrate()` upgrades a v1 file transparently —
it already runs `executescript(_SCHEMA)` then stamps `user_version`):

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

New `Database` API. The small return types (`Heartbeat`, `WatchHealth`,
`NotificationRow`, `SectionRow`) are frozen dataclasses defined in `db.py`
alongside the class:

- `Database(path, *, read_only: bool = False)` — when `read_only` is true, open
  with `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` and **skip
  `migrate()`** (no writes, no `user_version` bump). A missing file raises
  `sqlite3.OperationalError`; the web layer catches that.
- `write_heartbeat(cycle_count: int) -> None` — upsert the single row
  (`id = 1`) with `updated_at = datetime('now')`.
- `get_heartbeat() -> Heartbeat | None` — `Heartbeat(updated_at: str,
  cycle_count: int)` or `None` if the row/table is absent.
- `upsert_watch_health(watch: Watch, *, ok: bool, error: str = "") -> None` —
  `ok=True`: set `consecutive_failures = 0`, `last_success_at = datetime('now')`
  (leave `last_error*` as-is for "recovered" context). `ok=False`: increment
  `consecutive_failures`, set `last_error = error`, `last_error_at =
  datetime('now')`.
- `get_watch_health() -> dict[tuple[str, str], WatchHealth]` keyed by
  `(course_id, term_id)`; `WatchHealth(consecutive_failures, last_success_at,
  last_error, last_error_at)`.
- `get_section_rows(watch: Watch) -> list[SectionRow]` — like `get_snapshots`
  but returns rows that also carry `updated_at`; used only by the web layer so
  `SectionSnapshot` stays unchanged. `SectionRow(section_id, total_seats,
  open_seats, waitlist, updated_at)`, ordered by `section_id`.
- `recent_notifications(limit: int = 50) -> list[NotificationRow]` — newest
  first; `NotificationRow(sent_at, course_id, term_id, section_id, open_seats,
  channel, status, detail)`.

The existing `DatabaseError` test is updated to seed `user_version = 99` (still
`> SCHEMA_VERSION` now that it is 2).

### Engine changes (`engine.py`) — additive only

- `_process_watch(...)`: change the exception clause to
  `except (ScrapeError, requests.RequestException) as exc:` and, in it, call
  `db.upsert_watch_health(watch, ok=False, error=str(exc))` (alongside the
  existing log + `failure_counts` bump + threshold alert). On the success path
  (after `failure_counts[key] = 0`) call
  `db.upsert_watch_health(watch, ok=True)`.
- `run(...)`: keep a local `cycle_count` starting at 0; increment it at the top
  of each `while` iteration; after the watch `for` loop and **before** the
  `if once or _stop: return` check, call `db.write_heartbeat(cycle_count)` — so
  `check-once` (`once=True`) records exactly one heartbeat at `cycle_count = 1`.
- Nothing about detection, notification, de-dupe, signal handling, or the
  `POLITE_DELAY` / jitter timing changes. Every existing engine test must still
  pass unchanged; new assertions are added for the heartbeat / health rows.

### Web module (`web.py`)

`create_app(config: AppConfig) -> fastapi.FastAPI` — factory so tests pass a
config pointing at a temp DB.

- `Jinja2Templates(directory=<package>/templates)`.
- A per-request dependency opens `Database(config.db_path, read_only=True)` and
  closes it after the response. If the open (or any read) raises
  `sqlite3.OperationalError` (missing file, missing table on a not-yet-migrated
  v1 DB), the route renders the **guidance page** instead: HTTP 200, "No watcher
  data yet — run `testudo-watch run` to start polling."

Routes:

| Route | Response |
|---|---|
| `GET /` | `dashboard.html` — page shell, HTMX script, three fragment containers that self-load |
| `GET /fragments/status` | `_status.html` |
| `GET /fragments/watches` | `_watches.html` |
| `GET /fragments/notifications` | `_notifications.html` |

View-model builders (pure functions in `web.py`, no FastAPI types, unit-testable
directly):

- `build_status_view(db, config, *, now) -> StatusView` — reads `get_heartbeat`
  and `get_watch_health`. `STALE_GRACE_SECONDS = 20` is a module constant in
  `web.py`; `stale = heartbeat is None or age > (2 *
  config.poll_interval_seconds + STALE_GRACE_SECONDS)` (no import from
  `engine`). Produces: `last_poll_age` (humanized or `None`), `cycle_count`,
  `stale: bool`, `unhealthy: list[(course_id, term_id, consecutive_failures,
  last_error)]` for watches with `consecutive_failures > 0`.
- `build_watches_view(db, *, now) -> list[WatchView]` — `get_active_watches()`,
  and for each `get_section_rows(watch)`. Per watch: course, term, the watched
  section ids (or `"any section"`), and per section
  `SectionView(section_id, open_seats, total_seats, waitlist, is_open =
  open_seats > 0, updated_age = humanize_age(parse_db_utc(updated_at), now))`.
- `build_notifications_view(db, *, now, limit=50) -> list[NotificationView]` —
  `recent_notifications(limit)`, each with humanized `sent_at` and a
  `status` badge class (`sent` / `failed` / `health` / `health-failed`).

`web_time.py`: `parse_db_utc(s: str) -> datetime` (interpret `db`'s
`"%Y-%m-%d %H:%M:%S"` as UTC) and `humanize_age(then: datetime, now: datetime)
-> str` (`"8s ago"`, `"4m ago"`, `"2h ago"`, `"3d ago"`). `now` is always
injected so tests are deterministic; routes pass `datetime.now(timezone.utc)`.

### Templates

- `dashboard.html`: minimal inline CSS (a readable single-column layout, an
  amber banner style for stale/unhealthy, a green/red dot for section
  open/closed). One `<script>` tag loading an exact-pinned HTMX 2.x from a CDN
  (`https://unpkg.com/htmx.org@<pinned>/dist/htmx.min.js`); SRI attribute
  optional — if included, the implementer copies the real hash from that
  release, no placeholder. Three containers, each of which **server-renders its
  partial on first paint** via `{% include "_status.html" %}` etc. (so a
  freshly loaded page is already populated) AND carries
  `hx-get="/fragments/..." hx-trigger="every 15s" hx-swap="innerHTML"` to
  refresh on a timer. The route for `GET /` therefore builds all three
  view-models and passes them to `dashboard.html`.
- `_status.html`: "Watcher: last poll **6s ago** · cycle **412**" or the amber
  "⚠ last poll **4m ago** — watcher may be stopped" when `stale`, plus one line
  per `unhealthy` watch ("⚠ CMSC351 202601 — 3 consecutive failures:
  HTTP 503").
- `_watches.html`: a table, one block per watch, rows per section:
  `0101  ● open 2 / 200  (waitlist 0)   · updated 8s ago`, open rows visually
  flagged. "No watches configured yet." when empty.
- `_notifications.html`: a list, newest first: `00:28  CMSC351 0101  6 open
  · console · sent`. "No notifications yet." when empty.

### CLI (`cli.py`)

`_build_parser` gains a `serve` subparser: `--config` (default `watches.toml`),
`--db`, `--host` (default `127.0.0.1`), `--port` (default `8477`, `type=int`).
No `--verbose` — the `--verbose` loop becomes `if name not in ("list",
"serve")`. `serve` is handled like `list` (no notifier, no `build_session`):

```python
def _cmd_serve(config, *, host, port) -> int:
    import uvicorn
    from testudo_watch.web import create_app
    uvicorn.run(create_app(config), host=host, port=port)
    return 0
```

Wired in `main` after `configure_logging(config.log_dir)`, inside a
`try/except Exception -> _log.exception(...); return 2`. `KeyboardInterrupt`
(Ctrl-C) from `uvicorn.run` propagates as a clean shutdown (exit 0 via the
normal interpreter path) — do not map it to exit 2.

### Data flow

```
testudo-watch run  ──writes──> testudo_watch.db  <──reads (mode=ro)── testudo-watch serve ──renders──> browser
   (unchanged +                  watches                                   Database(…, read_only=True)      (HTMX polls
    heartbeat/health             section_snapshots                        build_*_view(db, config, now)      /fragments/*
    each cycle)                   notifications                            Jinja2 templates                   every 15s)
                                  watcher_heartbeat   (new)
                                  watch_health        (new)
```

## Error / degraded states

| Condition | Behavior |
|---|---|
| DB file does not exist | `GET /` and fragments render the guidance page, HTTP 200 |
| DB exists but is a pre-Phase-2 v1 file (new tables absent) | reads raise `sqlite3.OperationalError` → guidance page ("run the watcher once to upgrade"); next `testudo-watch run` migrates to v2 |
| No `watcher_heartbeat` row yet (watcher never completed a cycle) | status fragment: "watcher has not completed a poll yet" |
| Heartbeat older than `2·interval + jitter + 10s` | amber stale banner |
| A watch has `consecutive_failures > 0` | amber per-watch warning line with `last_error` |
| `watches.toml` missing / invalid at `serve` start | `ConfigError` → exit 1 (same as other commands, handled in `main` before dispatch) |
| Ctrl-C on `serve` | clean shutdown, exit 0 |

## Testing strategy

- `tests/test_web_time.py` — `humanize_age` across s/m/h/d boundaries with a
  fixed `now`; `parse_db_utc` returns a tz-aware UTC datetime.
- `tests/test_web.py` — `TestClient(create_app(config))`, `config.db_path` a
  `tmp_path` file seeded via a normal `Database` (write mode) with watches,
  section snapshots, notifications, a heartbeat, and one `watch_health` row:
  - `GET /` → 200, body contains a course id, a section's open count, `cycle`.
  - each `/fragments/*` → 200, returns the fragment (contains its table/list,
    not `<html>`).
  - seed an old heartbeat `updated_at` → `/fragments/status` contains the stale
    wording.
  - seed `watch_health(consecutive_failures=3, last_error="HTTP 503")` →
    `/fragments/status` shows it.
  - `config.db_path` pointing at a nonexistent file → `GET /` 200 with the
    guidance text, no 500.
- `tests/test_db.py` — new: fresh DB has `user_version == 2` and the two new
  tables; `write_heartbeat` / `get_heartbeat` round-trip and the `id = 1`
  upsert; `upsert_watch_health` ok→fail→fail→ok transitions and `get_watch_health`;
  `recent_notifications` ordering + limit; `read_only=True` on a missing file
  raises `sqlite3.OperationalError`; `read_only=True` never bumps `user_version`.
  The `DatabaseError` test seeds `user_version = 99`.
- `tests/test_engine.py` — after `run(once=True)` with a fake fetch: `get_heartbeat()`
  is non-`None`, `cycle_count == 1`; `get_watch_health()` for the watch has
  `consecutive_failures == 0` and `last_success_at` set. With a fetch that
  raises: `consecutive_failures >= 1` and `last_error` populated. Existing
  engine tests unchanged and still green.
- `tests/test_cli.py` — `testudo-watch serve --help` builds; `_cmd_serve` with
  `uvicorn.run` and `create_app` monkeypatched asserts `create_app` gets the
  config and `uvicorn.run` gets `host` / `port` from the parsed args, returns 0.
  No socket is bound.
- Full suite target: all Phase 1 tests still pass; new tests added; `pytest`
  output stays at 0 warnings (the `filterwarnings` entry already covers bs4).

## Definition of done (Phase 2)

- `pip install -e ".[dev]"` pulls in fastapi / uvicorn / jinja2 / httpx.
- With `testudo-watch run` polling in one terminal, `testudo-watch serve` in
  another serves `http://127.0.0.1:8477/` showing the watches, live-updating
  seat counts, recent notifications, and "last poll Ns ago / cycle N".
- Stopping `testudo-watch run` makes the dashboard show the stale banner within
  ~2 poll intervals.
- Running `serve` with no database yet shows the guidance page, not an error.
- `pytest` passes, 0 warnings.
- `README.md` documents the `serve` command.
