# Testudo Watch — Phase 1 Design (Make It Functional)

Date: 2026-09-09
Status: Approved design, pending spec review

## Context

The repo currently contains a single flat script, `send_sms.py`, that tries to
watch a UMD Testudo Schedule of Classes page for a hardcoded course and text a
Twilio number when a section opens. It does not work:

- `requests.get(url)` runs once, outside the loop, so every "before" and "after"
  comparison parses the identical stale HTML. An opening is never detected.
- Detection compares BeautifulSoup result objects (markup equality) rather than
  parsed seat counts.
- Section targeting uses brittle `nth-child(1)` / `nth-child(4)` positional
  selectors.
- The first transient exception sends an "It broke..." SMS and exits permanently.
- No dependency manifest, no logging, no tests, everything hardcoded.

## Goal

Phase 1 delivers a working, testable, configuration-driven watcher engine with
persistent state and structured logging. No UI in this phase, but the engine is
structured so a local web app (Phase 2) can import it directly.

Out of scope for Phase 1: the web UI, editing watches at runtime, multi-user
support, packaging/installers, notification channels beyond SMS and console.

## Roadmap (context only; not built in Phase 1)

- Phase 2: local web app (FastAPI) that imports the engine, runs the loop as a
  background task, and serves a browser UI to view watches and status.
- Phase 3: edit watches from the UI (SQLite becomes the source of truth for
  watches; `watches.toml` becomes an optional import), notification history
  views, additional notifier channels.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Phase 1 scope | Full skeleton now: modular package, config-driven watch list, SQLite state, structured logging. Everything except the UI. |
| Trigger rule | Notify when a watched section's Open seat count transitions `0 -> >0`. Waitlist ignored. |
| Watch unit | Course (`course_id` + `term_id`) plus an optional list of section IDs. Empty list = notify if any section opens. |
| State store | SQLite (stdlib `sqlite3`), single file. |
| Notifier | `Notifier` interface with two implementations: Twilio SMS and console/log. Config selects one. |
| Eventual UI | Local web app (FastAPI). Engine stays importable and UI-agnostic in Phase 1. |
| Concurrency | Synchronous, sequential loop over watches. No async. |

## Architecture

### Package layout

```
pyproject.toml            # project metadata + dependencies + console-script entry point
watches.toml              # example watch config, committed
.env.example              # Twilio credential template
README.md                 # rewritten for the new usage
src/testudo_watch/
  __init__.py
  config.py               # load + validate app settings and watches.toml
  models.py               # dataclasses: Watch, SectionSnapshot, OpeningEvent
  db.py                   # SQLite schema, migration, CRUD
  scraper.py              # fetch Testudo page + parse into SectionSnapshot list
  diff.py                 # compare prior vs current snapshots -> OpeningEvent list
  engine.py               # the watch loop; orchestrates scraper + diff + db + notifier
  logging_setup.py        # configure stdlib logging (console + rotating file)
  cli.py                  # argparse entry point: run / check-once / list
  notifier/
    __init__.py           # Notifier protocol + build_notifier() factory
    sms.py                # TwilioNotifier
    console.py            # ConsoleNotifier
tests/
  conftest.py
  fixtures/
    section_open.html     # real Testudo HTML, a course with an open section
    section_closed.html   # real Testudo HTML, all sections closed
  test_config.py
  test_scraper.py
  test_diff.py
  test_db.py
  test_engine.py
```

`send_sms.py` is deleted. Git history preserves it.

### Dependencies

Runtime: `requests`, `beautifulsoup4`, `lxml`, `twilio`, `python-dotenv`.
Dev: `pytest`.

Managed via `pyproject.toml` (PEP 621). A `requirements.txt` is also generated
for users who prefer plain `pip install -r`. Target Python 3.11+ (`tomllib` is
stdlib there; used to read `watches.toml`).

### Configuration

`watches.toml` (authored by the user, hand-edited in Phase 1):

```toml
poll_interval_seconds = 30      # floor-enforced to >= 15
notifier = "console"            # "console" | "sms"

[[watch]]
course_id = "CMSC351"
term_id = "202408"
sections = ["0101", "0201"]     # empty or omitted = any section

[[watch]]
course_id = "MATH240"
term_id = "202408"
sections = []
```

- `config.py` reads this with `tomllib`, validates types and required fields,
  applies the `poll_interval_seconds` floor, and returns an `AppConfig`
  dataclass plus a `list[Watch]`.
- Validation errors raise a clear `ConfigError` with the offending key; the CLI
  prints it and exits non-zero.
- Twilio credentials stay in `.env` (`ACCOUNT_SID`, `AUTH_TOKEN`, `TO_NUMBER`,
  `FROM_NUMBER`), loaded via `python-dotenv`. Only required when
  `notifier = "sms"`. `.env.example` documents them. `.gitignore` already
  excludes `.env`.

### Data model (`models.py`)

```python
@dataclass(frozen=True)
class Watch:
    course_id: str
    term_id: str
    sections: tuple[str, ...]      # empty = all sections

@dataclass(frozen=True)
class SectionSnapshot:
    course_id: str
    term_id: str
    section_id: str
    total_seats: int
    open_seats: int
    waitlist: int

@dataclass(frozen=True)
class OpeningEvent:
    snapshot: SectionSnapshot      # the section that just opened
```

### Database (`db.py`)

Single SQLite file, path from `AppConfig` (default `testudo_watch.db` in the
working directory). Schema created/migrated on open via a `schema_version`
pragma check.

Tables:

- `watches` — `(course_id, term_id, sections_csv, active)`, primary key
  `(course_id, term_id)`. Upserted from `watches.toml` on every `run` start:
  entries in the file are marked active; entries previously present but now
  absent are marked `active = 0` (kept for history, not polled).
- `section_snapshots` — `(course_id, term_id, section_id, total_seats,
  open_seats, waitlist, updated_at)`, primary key
  `(course_id, term_id, section_id)`. Holds the most recent seen state per
  section. This is what `diff` reads to decide whether an opening is new.
- `notifications` — `(id, course_id, term_id, section_id, open_seats, sent_at,
  channel, status, detail)`. Append-only log of every notification attempt.

CRUD functions: `sync_watches`, `get_active_watches`, `get_snapshots(watch)`,
`upsert_snapshots(list)`, `record_notification(...)`, plus
`recent_open_episode_notified(course, term, section)` for de-dupe (see below).

### Scraper (`scraper.py`)

`fetch_sections(session, course_id, term_id) -> list[SectionSnapshot]`

- Builds the Testudo Schedule of Classes URL for a single course + term with
  `_openSectionsOnly` **off** (we need to see closed sections to detect the
  `0 -> >0` transition).
- Uses a passed-in `requests.Session` with a real browser `User-Agent` and a
  connect/read timeout (10s).
- Non-200 raises `ScrapeError`.
- Parses with BeautifulSoup + `lxml`. For each section block on the page,
  extracts `section_id` and the "Seats (Total: N, Open: M, Waitlist: W)" numbers
  from the section's seat elements. Parsing targets Testudo's semantic classes
  (`.section`, `.section-id`, `.seats-info` / `.open-seats-count` etc. — exact
  selectors pinned against the committed fixtures), not positional `nth-child`.
- Returns one `SectionSnapshot` per section found. Missing/unparseable numbers
  for a section raise `ScrapeError` (fail loud rather than silently treat as 0).

### Diff (`diff.py`)

`detect_openings(prev: dict[str, SectionSnapshot], curr: list[SectionSnapshot],
watch: Watch) -> list[OpeningEvent]`

- `prev` is keyed by `section_id` (from the DB); `curr` is fresh from the
  scraper.
- Restrict to `watch.sections` if non-empty, else all sections in `curr`.
- Emit an `OpeningEvent` for a section when:
  - it has a prior snapshot with `open_seats == 0` and now `open_seats > 0`, or
  - it has no prior snapshot and `open_seats > 0` (first sighting already open).
- No event while `open_seats` stays `> 0` across cycles (de-dupe by state: the
  prior snapshot is only overwritten with `> 0` after we've notified, so the
  next cycle's `prev.open_seats > 0` suppresses a repeat). When it returns to
  `0`, the episode resets and a later reopen notifies again.

### Engine (`engine.py`)

`run(config, watches, db, notifier, *, once=False, clock=time)`

Loop:

1. For each active watch:
   - `try`: `snapshots = scraper.fetch_sections(session, ...)`.
   - `prev = db.get_snapshots(watch)`.
   - `events = diff.detect_openings(prev, snapshots, watch)`.
   - For each event: `notifier.send(message)` where message names course,
     section, and open seat count; `db.record_notification(...)` with the
     resulting status. Track which section IDs had a failed send this cycle.
   - `db.upsert_snapshots(...)` for every section in `snapshots` **except** those
     with a failed send this cycle (done after notifying, so a crash between
     detect and notify re-tries next cycle rather than losing the event, and a
     failed notification is retried next cycle because that section's prior
     `open_seats == 0` is left intact).
   - reset this watch's consecutive-failure counter to 0.
   - `except (ScrapeError, requests.RequestException) as e`: log at ERROR with
     traceback, increment the watch's consecutive-failure counter. If it reaches
     `FAILURE_ALERT_THRESHOLD` (default 5), send one "watcher unhealthy" notice
     via the notifier and record it, then keep the counter pinned so it does not
     re-alert until a success resets it.
   - Sleep `POLITE_DELAY_SECONDS` (default 3) between watches.
2. If `once`: return after one pass.
3. Sleep `poll_interval_seconds` plus jitter of `random.uniform(0, 5)`.

Failure counters are per-watch, in-memory (reset on restart). This is
intentional: a restart should re-probe cleanly, not inherit a stale unhealthy
state.

Signal handling: `run` installs SIGINT/SIGTERM handlers that set a stop flag;
the loop checks it between watches and at the top of each cycle, then returns so
the CLI can close the DB and exit 0.

### Notifier (`notifier/`)

```python
class Notifier(Protocol):
    def send(self, message: str) -> None: ...   # raises NotifierError on failure
```

- `ConsoleNotifier`: logs the message at WARNING and prints to stdout. Never
  raises. Used for local testing without spending SMS.
- `TwilioNotifier`: wraps `twilio.rest.Client`; `send` calls
  `messages.create(to, from_, body)`. Wraps Twilio exceptions in
  `NotifierError`. Constructed from `.env` values; raises `ConfigError` at
  startup if any are missing.
- `build_notifier(config) -> Notifier`: factory selecting the implementation
  from `config.notifier`.

The engine catches `NotifierError`, logs it, and records the notification row
with `status = "failed"` and the error detail. A failed send does **not** update
the snapshot for that section, so the next cycle retries the notification.

### Logging (`logging_setup.py`)

stdlib `logging`, configured once at CLI start:

- Console handler: INFO by default, DEBUG with `--verbose`. Human-readable
  format `%(asctime)s %(levelname)s %(name)s: %(message)s`.
- Rotating file handler: `logs/testudo_watch.log`, INFO, 1 MB x 5 backups.
- Levels in use: INFO for one per-cycle summary line per watch (section -> open
  count), WARNING for detected openings and notifier console output, ERROR (with
  `exc_info`) for scrape/notifier failures.

### CLI (`cli.py`)

`argparse`, exposed as console script `testudo-watch`:

- `testudo-watch run [--config watches.toml] [--db PATH] [--verbose]` — load
  config, set up logging, open DB, sync watches, build notifier, call
  `engine.run(...)`.
- `testudo-watch check-once [same flags]` — same but `engine.run(..., once=True)`;
  for smoke-testing a real course without committing to the loop.
- `testudo-watch list [--config] [--db]` — print each configured watch and its
  last-seen per-section seat counts from `section_snapshots`.

Exit codes: 0 normal / clean shutdown; 1 config error; 2 unexpected fatal error
(logged with traceback).

## Data flow (happy path)

```
watches.toml ──load──> config.AppConfig + [Watch]
                          │
cli run ──────────────────┼──> logging_setup.configure()
                          ├──> db.open() + db.migrate() + db.sync_watches()
                          ├──> notifier = build_notifier(config)
                          └──> engine.run(...)
                                 loop:
                                   for watch in db.get_active_watches():
                                     curr = scraper.fetch_sections(session, watch)
                                     prev = db.get_snapshots(watch)
                                     events = diff.detect_openings(prev, curr, watch)
                                     for e in events:
                                       notifier.send(format(e))
                                       db.record_notification(e, status)
                                     db.upsert_snapshots(curr)
                                   sleep(poll_interval + jitter)
```

## Error handling summary

| Failure | Behavior |
|---|---|
| Bad `watches.toml` | `ConfigError`, CLI prints offending key, exit 1. Loop never starts. |
| Missing Twilio env with `notifier = "sms"` | `ConfigError` at startup, exit 1. |
| Testudo non-200 / timeout / connection error | Logged at ERROR with traceback; that watch skipped this cycle; consecutive-failure counter++. Loop continues. |
| Section seat numbers unparseable | `ScrapeError`, treated same as above (fail loud, skip watch this cycle). |
| 5 consecutive failures for one watch | One "watcher unhealthy" notice sent; counter pinned until a success. |
| Notifier send fails | `NotifierError` logged; `notifications` row `status = "failed"`; snapshot not updated so next cycle retries. |
| SIGINT / SIGTERM | Stop flag set; loop exits between watches; DB closed; exit 0. |

## Testing strategy

- `test_config.py` — valid file parses; floor applied to `poll_interval_seconds`;
  each validation error path raises `ConfigError` naming the key.
- `test_scraper.py` — parse `fixtures/section_open.html` and
  `fixtures/section_closed.html`; assert exact `SectionSnapshot` lists
  (section IDs and Total/Open/Waitlist numbers). Network is never hit (parse a
  string, or a `requests` response mocked with `responses`/monkeypatch). One
  test asserts non-200 raises `ScrapeError`.
- `test_diff.py` — table of `(prev, curr)` cases: `0 -> >0` emits; `>0 -> >0`
  silent; no-prior + `>0` emits; no-prior + `0` silent; `>0 -> 0` silent and
  resets; section filtering honored.
- `test_db.py` — migrate on a `tmp_path` DB; `sync_watches` marks removed
  entries inactive; snapshot upsert round-trips; `record_notification` appends.
- `test_engine.py` — one `once=True` pass with a fake scraper returning canned
  snapshots, a fake notifier capturing messages, and a temp DB: assert the right
  messages sent, `notifications` rows written, snapshots persisted. A second test
  makes the fake scraper raise and asserts the loop swallows it and increments
  the counter.

Fixtures are real Testudo HTML captured once and committed, trimmed to the
sections container to keep them small.

## Definition of done (Phase 1)

- `pip install -e .` (or `pip install -r requirements.txt`) sets up all deps.
- `testudo-watch check-once` against a real current course prints parsed
  per-section seat counts and, with `notifier = "console"`, logs any opening.
- `testudo-watch run` loops without dying on transient network errors, writes
  `logs/testudo_watch.log`, and persists state to SQLite across restarts without
  re-notifying for a still-open section.
- `pytest` passes.
- `send_sms.py` removed; `README.md` updated with the new setup and usage.
