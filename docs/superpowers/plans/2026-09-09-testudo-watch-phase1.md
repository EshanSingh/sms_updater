# Testudo Watch — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken `send_sms.py` script with a configuration-driven, tested watcher engine that polls UMD Testudo for open seats in watched course sections, persists state in SQLite, and notifies via a pluggable Notifier (console or Twilio SMS).

**Architecture:** A `testudo_watch` package under `src/`, split into focused modules: `config` (load/validate `watches.toml`), `models` (dataclasses), `db` (SQLite state + audit log), `scraper` (fetch + parse Testudo's sections endpoint), `diff` (detect `0 -> >0` open-seat transitions), `notifier/` (Protocol + console + Twilio impls), `engine` (the poll loop), `logging_setup`, and `cli` (argparse: `run` / `check-once` / `list`). The engine is imported and driven by the CLI in Phase 1; a Phase 2 web app will import the same engine.

**Tech Stack:** Python 3.11+, `requests`, `beautifulsoup4`, `lxml`, `twilio`, `python-dotenv`; `pytest` for tests; `sqlite3` and `tomllib` from the stdlib.

**Spec:** `docs/superpowers/specs/2026-09-09-testudo-watch-phase1-design.md`

## Global Constraints

- Python 3.11 or newer (relies on stdlib `tomllib`).
- Runtime dependencies limited to: `requests`, `beautifulsoup4`, `lxml`, `twilio`, `python-dotenv`. Dev dependency: `pytest`. Do not add others.
- All source lives under `src/testudo_watch/`. All tests under `tests/`.
- Console script entry point is `testudo-watch` → `testudo_watch.cli:main`.
- `poll_interval_seconds` has a hard floor of 15; values below are raised to 15 with a logged warning.
- The poll loop must never exit on a scrape or notifier error — only on config error (before the loop), SIGINT/SIGTERM, or an unexpected fatal exception.
- Twilio credentials come only from environment / `.env` (`ACCOUNT_SID`, `AUTH_TOKEN`, `TO_NUMBER`, `FROM_NUMBER`), never from `watches.toml`.
- Testudo access: one course per HTTP request, a real `User-Agent`, a 10-second timeout, and a polite delay between requests.
- Trigger rule: notify when a watched section's open-seat count goes from `0` (or no prior record) to `> 0`. Waitlist is never a trigger.
- Every code step is TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.

---

## File Structure

**Created:**
- `pyproject.toml` — project metadata, dependencies, entry point, pytest config
- `requirements.txt` — plain-pip mirror of runtime deps
- `.env.example` — Twilio credential template
- `watches.toml` — example watch configuration (committed)
- `src/testudo_watch/__init__.py` — package marker, version
- `src/testudo_watch/models.py` — `Watch`, `SectionSnapshot`, `OpeningEvent`
- `src/testudo_watch/config.py` — `AppConfig`, `ConfigError`, `load_config`
- `src/testudo_watch/db.py` — `Database` (schema, migrate, CRUD)
- `src/testudo_watch/scraper.py` — `ScrapeError`, `build_session`, `parse_sections`, `fetch_sections`
- `src/testudo_watch/diff.py` — `detect_openings`
- `src/testudo_watch/notifier/__init__.py` — `Notifier` Protocol, `NotifierError`, `build_notifier`
- `src/testudo_watch/notifier/console.py` — `ConsoleNotifier`
- `src/testudo_watch/notifier/sms.py` — `TwilioNotifier`
- `src/testudo_watch/logging_setup.py` — `configure_logging`
- `src/testudo_watch/engine.py` — `format_opening`, `run`
- `src/testudo_watch/cli.py` — `main` and subcommand handlers
- `tests/__init__.py` — empty
- `tests/conftest.py` — shared fixtures
- `tests/fixtures/sections_sample.html` — trimmed real Testudo markup (one open + one closed section)
- `tests/test_config.py`, `tests/test_db.py`, `tests/test_scraper.py`, `tests/test_diff.py`, `tests/test_notifier.py`, `tests/test_engine.py`, `tests/test_cli.py`

**Modified:**
- `.gitignore` — add Python / venv / db / logs ignores
- `README.md` — rewrite for new install + usage

**Deleted:**
- `send_sms.py`

---

## Task 1: Project scaffold and packaging

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.env.example`, `watches.toml`, `src/testudo_watch/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_smoke.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable `testudo_watch` package importable as `import testudo_watch`; `testudo_watch.__version__` string; a working `pytest` run.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "testudo-watch"
version = "0.1.0"
description = "Watch UMD Testudo for open seats in course sections and get notified."
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "twilio>=9.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
testudo-watch = "testudo_watch.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

- [ ] **Step 2: Write `requirements.txt`**

```
requests>=2.31
beautifulsoup4>=4.12
lxml>=5.0
twilio>=9.0
python-dotenv>=1.0
```

- [ ] **Step 3: Write `.env.example`**

```
# Twilio credentials — only needed when watches.toml sets notifier = "sms"
ACCOUNT_SID=your_twilio_account_sid
AUTH_TOKEN=your_twilio_auth_token
TO_NUMBER=+15555550123
FROM_NUMBER=+15555550188
```

- [ ] **Step 4: Write `watches.toml`**

```toml
# How often to poll Testudo, in seconds. Hard minimum is 15.
poll_interval_seconds = 30

# "console" logs openings locally; "sms" sends Twilio texts (needs .env).
notifier = "console"

# Optional overrides (defaults shown):
# db_path = "testudo_watch.db"
# log_dir = "logs"

# One [[watch]] block per course. Set term_id to the current Testudo term.
# Leave sections empty (or omit) to be notified when ANY section opens.
[[watch]]
course_id = "CMSC351"
term_id = "202601"
sections = ["0101", "0201"]

[[watch]]
course_id = "MATH240"
term_id = "202601"
sections = []
```

- [ ] **Step 5: Write `src/testudo_watch/__init__.py`**

```python
"""Watch UMD Testudo for open seats in course sections."""

__version__ = "0.1.0"
```

- [ ] **Step 6: Create empty `tests/__init__.py`**

(empty file)

- [ ] **Step 7: Write `tests/conftest.py`**

```python
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
```

- [ ] **Step 8: Write `tests/test_smoke.py`**

```python
import testudo_watch


def test_package_has_version():
    assert testudo_watch.__version__ == "0.1.0"
```

- [ ] **Step 9: Replace `.gitignore` contents**

```
.env

# Python
__pycache__/
*.py[cod]
*.egg-info/
build/
dist/

# venv
.venv/
venv/

# runtime artifacts
*.db
logs/

# tooling
.pytest_cache/
```

- [ ] **Step 10: Install and run tests**

Run:
```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```
Expected: `test_package_has_version` PASSES.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml requirements.txt .env.example watches.toml src/testudo_watch/__init__.py tests/__init__.py tests/conftest.py tests/test_smoke.py .gitignore
git commit -m "chore: scaffold testudo_watch package and packaging"
```

---

## Task 2: Data models

**Files:**
- Create: `src/testudo_watch/models.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Watch(course_id: str, term_id: str, sections: tuple[str, ...] = ())` — frozen dataclass.
  - `SectionSnapshot(course_id: str, term_id: str, section_id: str, total_seats: int, open_seats: int, waitlist: int)` — frozen dataclass.
  - `OpeningEvent(snapshot: SectionSnapshot)` — frozen dataclass.

- [ ] **Step 1: Write the failing test — `tests/test_models.py`**

```python
import dataclasses

import pytest

from testudo_watch.models import OpeningEvent, SectionSnapshot, Watch


def test_watch_defaults_sections_to_empty_tuple():
    w = Watch(course_id="CMSC351", term_id="202601")
    assert w.sections == ()


def test_watch_is_frozen():
    w = Watch(course_id="CMSC351", term_id="202601")
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.course_id = "CMSC330"


def test_section_snapshot_holds_seat_counts():
    s = SectionSnapshot(
        course_id="CMSC351",
        term_id="202601",
        section_id="0101",
        total_seats=200,
        open_seats=6,
        waitlist=0,
    )
    assert (s.total_seats, s.open_seats, s.waitlist) == (200, 6, 0)


def test_opening_event_wraps_snapshot():
    s = SectionSnapshot("CMSC351", "202601", "0101", 200, 6, 0)
    assert OpeningEvent(snapshot=s).snapshot is s
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.models'`.

- [ ] **Step 3: Write `src/testudo_watch/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Watch:
    course_id: str
    term_id: str
    sections: tuple[str, ...] = field(default_factory=tuple)


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
    snapshot: SectionSnapshot
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_models.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/models.py tests/test_models.py
git commit -m "feat: add core data models"
```

---

## Task 3: Config loader

**Files:**
- Create: `src/testudo_watch/config.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `Watch` from `testudo_watch.models`.
- Produces:
  - `ConfigError(Exception)`.
  - `MIN_POLL_INTERVAL_SECONDS = 15`, `DEFAULT_POLL_INTERVAL_SECONDS = 30`.
  - `AppConfig(poll_interval_seconds: int, notifier: str, db_path: str, log_dir: str)` — frozen dataclass. `notifier` is `"console"` or `"sms"`.
  - `load_config(path: str | pathlib.Path) -> tuple[AppConfig, list[Watch]]`.

- [ ] **Step 1: Write the failing test — `tests/test_config.py`**

```python
import textwrap
from pathlib import Path

import pytest

from testudo_watch.config import AppConfig, ConfigError, load_config


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "watches.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_loads_valid_config(tmp_path):
    path = write(
        tmp_path,
        """
        poll_interval_seconds = 30
        notifier = "console"

        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        sections = ["0101", "0201"]

        [[watch]]
        course_id = "MATH240"
        term_id = "202601"
        """,
    )
    config, watches = load_config(path)
    assert config == AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path="testudo_watch.db",
        log_dir="logs",
    )
    assert watches[0].course_id == "CMSC351"
    assert watches[0].sections == ("0101", "0201")
    assert watches[1].sections == ()


def test_poll_interval_floor_is_enforced(tmp_path):
    path = write(
        tmp_path,
        """
        poll_interval_seconds = 3
        notifier = "console"
        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        """,
    )
    config, _ = load_config(path)
    assert config.poll_interval_seconds == 15


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_bad_notifier_raises_config_error_naming_key(tmp_path):
    path = write(
        tmp_path,
        """
        notifier = "carrier-pigeon"
        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        """,
    )
    with pytest.raises(ConfigError, match="notifier"):
        load_config(path)


def test_watch_missing_required_key_raises_naming_key(tmp_path):
    path = write(
        tmp_path,
        """
        notifier = "console"
        [[watch]]
        course_id = "CMSC351"
        """,
    )
    with pytest.raises(ConfigError, match="term_id"):
        load_config(path)


def test_no_watches_raises_config_error(tmp_path):
    path = write(tmp_path, 'notifier = "console"\n')
    with pytest.raises(ConfigError, match="watch"):
        load_config(path)


def test_sections_must_be_list_of_strings(tmp_path):
    path = write(
        tmp_path,
        """
        notifier = "console"
        [[watch]]
        course_id = "CMSC351"
        term_id = "202601"
        sections = "0101"
        """,
    )
    with pytest.raises(ConfigError, match="sections"):
        load_config(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.config'`.

- [ ] **Step 3: Write `src/testudo_watch/config.py`**

```python
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from testudo_watch.models import Watch

MIN_POLL_INTERVAL_SECONDS = 15
DEFAULT_POLL_INTERVAL_SECONDS = 30
_VALID_NOTIFIERS = {"console", "sms"}


class ConfigError(Exception):
    """Raised when watches.toml is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class AppConfig:
    poll_interval_seconds: int
    notifier: str
    db_path: str
    log_dir: str


def load_config(path: str | Path) -> tuple[AppConfig, list[Watch]]:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc

    notifier = raw.get("notifier", "console")
    if notifier not in _VALID_NOTIFIERS:
        raise ConfigError(
            f"notifier must be one of {sorted(_VALID_NOTIFIERS)}, got {notifier!r}"
        )

    interval = raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    if not isinstance(interval, int) or isinstance(interval, bool):
        raise ConfigError("poll_interval_seconds must be an integer")
    if interval < MIN_POLL_INTERVAL_SECONDS:
        interval = MIN_POLL_INTERVAL_SECONDS

    config = AppConfig(
        poll_interval_seconds=interval,
        notifier=notifier,
        db_path=str(raw.get("db_path", "testudo_watch.db")),
        log_dir=str(raw.get("log_dir", "logs")),
    )

    watch_entries = raw.get("watch", [])
    if not watch_entries:
        raise ConfigError("at least one [[watch]] entry is required")

    watches: list[Watch] = []
    for i, entry in enumerate(watch_entries):
        for key in ("course_id", "term_id"):
            if key not in entry:
                raise ConfigError(f"[[watch]] #{i + 1} is missing required key {key!r}")
        sections = entry.get("sections", [])
        if not isinstance(sections, list) or not all(
            isinstance(s, str) for s in sections
        ):
            raise ConfigError(
                f"[[watch]] #{i + 1} sections must be a list of strings"
            )
        watches.append(
            Watch(
                course_id=str(entry["course_id"]),
                term_id=str(entry["term_id"]),
                sections=tuple(sections),
            )
        )
    return config, watches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/config.py tests/test_config.py
git commit -m "feat: add watches.toml config loader with validation"
```

---

## Task 4: Database layer

**Files:**
- Create: `src/testudo_watch/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `Watch`, `SectionSnapshot` from `testudo_watch.models`.
- Produces `Database` with:
  - `Database(path: str | pathlib.Path)` — opens the connection and runs `migrate()`.
  - `close() -> None`.
  - `sync_watches(watches: list[Watch]) -> None` — upsert; entries absent from the list become `active = 0`.
  - `get_active_watches() -> list[Watch]`.
  - `get_snapshots(watch: Watch) -> dict[str, SectionSnapshot]` — keyed by `section_id`.
  - `upsert_snapshots(snapshots: Iterable[SectionSnapshot]) -> None`.
  - `record_notification(snapshot: SectionSnapshot, *, channel: str, status: str, detail: str = "") -> None`.

- [ ] **Step 1: Write the failing test — `tests/test_db.py`**

```python
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot, Watch


def make_db(tmp_path):
    return Database(tmp_path / "state.db")


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.db'`.

- [ ] **Step 3: Write `src/testudo_watch/db.py`**

```python
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from testudo_watch.models import SectionSnapshot, Watch

SCHEMA_VERSION = 1

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
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/db.py tests/test_db.py
git commit -m "feat: add SQLite state and notification-log layer"
```

---

## Task 5: Scraper

**Files:**
- Create: `src/testudo_watch/scraper.py`, `tests/fixtures/sections_sample.html`, `tests/test_scraper.py`

**Interfaces:**
- Consumes: `SectionSnapshot` from `testudo_watch.models`.
- Produces:
  - `ScrapeError(Exception)`.
  - `SECTIONS_URL = "https://app.testudo.umd.edu/soc/{term_id}/sections"`.
  - `USER_AGENT`, `REQUEST_TIMEOUT = 10`.
  - `build_session() -> requests.Session` — session with the `User-Agent` header set.
  - `parse_sections(html: str, course_id: str, term_id: str) -> list[SectionSnapshot]` — raises `ScrapeError` if no section blocks or unparseable seat numbers.
  - `fetch_sections(session: requests.Session, course_id: str, term_id: str) -> list[SectionSnapshot]` — GET + parse; raises `ScrapeError` on non-200 or request failure.

- [ ] **Step 1: Write `tests/fixtures/sections_sample.html`**

Trimmed from a live `GET https://app.testudo.umd.edu/soc/202508/sections?courseIds=CMSC351`. Section `0101` is open (6 seats); section `0201` is closed (0 seats, 12 on waitlist).

```html
<div>
  <div id="CMSC351" class="course-sections">
    <div class="sections-container">
      <div class="sections sixteen colgrid">
        <div class="section delivery-f2f">
          <input type="hidden" name="sectionId" value="0101" />
          <div class="section-info-container">
            <div class="section-id-container two columns">
              <span class="section-id">
                0101
              </span>
            </div>
            <div class="seats-info-group six columns">
              <span class="section-info-label">Seats</span>
              <span class="seats-info">
                <span class="total-seats">
                  (<span class="seats-info-label">Total:</span>
                  <span class="total-seats-count">200</span>,
                </span>
                <span class="open-seats has-open-seats">
                  <span class="seats-info-label">Open:</span>
                  <span class="open-seats-count">6</span>,
                </span>
                <span class="waitlist ">
                  <span class="seats-info-label"> Waitlist:</span>
                  <span class="waitlist-count">0</span>
                </span>
              </span>
            </div>
          </div>
        </div>
        <div class="section delivery-f2f">
          <input type="hidden" name="sectionId" value="0201" />
          <div class="section-info-container">
            <div class="section-id-container two columns">
              <span class="section-id">
                0201
              </span>
            </div>
            <div class="seats-info-group six columns">
              <span class="section-info-label">Seats</span>
              <span class="seats-info">
                <span class="total-seats">
                  (<span class="seats-info-label">Total:</span>
                  <span class="total-seats-count">90</span>,
                </span>
                <span class="open-seats">
                  <span class="seats-info-label">Open:</span>
                  <span class="open-seats-count">0</span>,
                </span>
                <span class="waitlist ">
                  <span class="seats-info-label"> Waitlist:</span>
                  <span class="waitlist-count">12</span>
                </span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Write the failing test — `tests/test_scraper.py`**

```python
import pytest
import requests

from testudo_watch.models import SectionSnapshot
from testudo_watch.scraper import (
    ScrapeError,
    build_session,
    fetch_sections,
    parse_sections,
)


@pytest.fixture
def sample_html(fixtures_dir):
    return (fixtures_dir / "sections_sample.html").read_text(encoding="utf-8")


def test_parse_returns_one_snapshot_per_section(sample_html):
    snaps = parse_sections(sample_html, "CMSC351", "202508")
    assert snaps == [
        SectionSnapshot("CMSC351", "202508", "0101", 200, 6, 0),
        SectionSnapshot("CMSC351", "202508", "0201", 90, 0, 12),
    ]


def test_parse_raises_when_no_sections():
    with pytest.raises(ScrapeError, match="no sections"):
        parse_sections("<div>nothing here</div>", "CMSC351", "202508")


def test_parse_raises_when_seat_count_missing():
    broken = """
    <div class="section">
      <input type="hidden" name="sectionId" value="0101" />
      <span class="seats-info"><span class="open-seats-count">x</span></span>
    </div>
    """
    with pytest.raises(ScrapeError):
        parse_sections(broken, "CMSC351", "202508")


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_fetch_sections_parses_ok(monkeypatch, sample_html):
    session = build_session()

    def fake_get(url, timeout):
        assert "202508/sections" in url
        assert timeout == 10
        return _FakeResponse(200, sample_html)

    monkeypatch.setattr(session, "get", fake_get)
    snaps = fetch_sections(session, "CMSC351", "202508")
    assert [s.section_id for s in snaps] == ["0101", "0201"]


def test_fetch_sections_raises_on_non_200(monkeypatch):
    session = build_session()
    monkeypatch.setattr(session, "get", lambda url, timeout: _FakeResponse(503, ""))
    with pytest.raises(ScrapeError, match="503"):
        fetch_sections(session, "CMSC351", "202508")


def test_fetch_sections_raises_on_request_exception(monkeypatch):
    session = build_session()

    def boom(url, timeout):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(session, "get", boom)
    with pytest.raises(ScrapeError):
        fetch_sections(session, "CMSC351", "202508")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_scraper.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.scraper'`.

- [ ] **Step 4: Write `src/testudo_watch/scraper.py`**

```python
from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from testudo_watch.models import SectionSnapshot

SECTIONS_URL = "https://app.testudo.umd.edu/soc/{term_id}/sections"
USER_AGENT = "Mozilla/5.0 (compatible; testudo-watch/0.1; +https://github.com/)"
REQUEST_TIMEOUT = 10


class ScrapeError(Exception):
    """Raised when Testudo cannot be fetched or its markup cannot be parsed."""


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _seat_count(section, class_name: str) -> int:
    node = section.find("span", class_=class_name)
    if node is None:
        raise ScrapeError(f"missing {class_name!r} in section markup")
    text = node.get_text(strip=True)
    try:
        return int(text)
    except ValueError as exc:
        raise ScrapeError(f"non-numeric {class_name!r}: {text!r}") from exc


def parse_sections(
    html: str, course_id: str, term_id: str
) -> list[SectionSnapshot]:
    soup = BeautifulSoup(html, "lxml")
    sections = soup.select("div.section")
    if not sections:
        raise ScrapeError(f"no sections found for {course_id} in term {term_id}")

    snapshots: list[SectionSnapshot] = []
    for section in sections:
        id_input = section.find("input", attrs={"name": "sectionId"})
        if id_input is not None and id_input.get("value"):
            section_id = id_input["value"].strip()
        else:
            id_span = section.find("span", class_="section-id")
            if id_span is None:
                raise ScrapeError("section block has no section id")
            section_id = id_span.get_text(strip=True)

        snapshots.append(
            SectionSnapshot(
                course_id=course_id,
                term_id=term_id,
                section_id=section_id,
                total_seats=_seat_count(section, "total-seats-count"),
                open_seats=_seat_count(section, "open-seats-count"),
                waitlist=_seat_count(section, "waitlist-count"),
            )
        )
    return snapshots


def fetch_sections(
    session: requests.Session, course_id: str, term_id: str
) -> list[SectionSnapshot]:
    url = SECTIONS_URL.format(term_id=term_id) + f"?courseIds={course_id}"
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise ScrapeError(f"request to {url} failed: {exc}") from exc
    if response.status_code != 200:
        raise ScrapeError(f"Testudo returned HTTP {response.status_code} for {url}")
    return parse_sections(response.text, course_id, term_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_scraper.py -q`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/testudo_watch/scraper.py tests/fixtures/sections_sample.html tests/test_scraper.py
git commit -m "feat: add Testudo sections scraper"
```

---

## Task 6: Diff / opening detection

**Files:**
- Create: `src/testudo_watch/diff.py`, `tests/test_diff.py`

**Interfaces:**
- Consumes: `Watch`, `SectionSnapshot`, `OpeningEvent` from `testudo_watch.models`.
- Produces: `detect_openings(prev: dict[str, SectionSnapshot], curr: list[SectionSnapshot], watch: Watch) -> list[OpeningEvent]`.
  - `prev` keyed by `section_id` (from `Database.get_snapshots`).
  - Emit an `OpeningEvent` for a section when `open_seats > 0` **and** (no prior snapshot **or** prior `open_seats == 0`).
  - If `watch.sections` is non-empty, only those section IDs are considered.

- [ ] **Step 1: Write the failing test — `tests/test_diff.py`**

```python
from testudo_watch.diff import detect_openings
from testudo_watch.models import SectionSnapshot, Watch

W_ALL = Watch("CMSC351", "202601", ())
W_ONE = Watch("CMSC351", "202601", ("0101",))


def snap(section_id, open_seats):
    return SectionSnapshot("CMSC351", "202601", section_id, 100, open_seats, 0)


def test_zero_to_positive_emits_event():
    prev = {"0101": snap("0101", 0)}
    curr = [snap("0101", 3)]
    events = detect_openings(prev, curr, W_ALL)
    assert [e.snapshot.section_id for e in events] == ["0101"]
    assert events[0].snapshot.open_seats == 3


def test_no_prior_and_open_emits_event():
    events = detect_openings({}, [snap("0101", 1)], W_ALL)
    assert [e.snapshot.section_id for e in events] == ["0101"]


def test_no_prior_and_closed_is_silent():
    assert detect_openings({}, [snap("0101", 0)], W_ALL) == []


def test_stays_open_is_silent():
    prev = {"0101": snap("0101", 5)}
    assert detect_openings(prev, [snap("0101", 4)], W_ALL) == []


def test_positive_to_zero_is_silent_and_resets():
    prev = {"0101": snap("0101", 5)}
    assert detect_openings(prev, [snap("0101", 0)], W_ALL) == []
    # After reset, a later reopen fires again.
    prev = {"0101": snap("0101", 0)}
    assert len(detect_openings(prev, [snap("0101", 2)], W_ALL)) == 1


def test_section_filter_limits_to_watched_ids():
    curr = [snap("0101", 3), snap("0201", 9)]
    events = detect_openings({}, curr, W_ONE)
    assert [e.snapshot.section_id for e in events] == ["0101"]


def test_multiple_sections_emit_multiple_events():
    curr = [snap("0101", 3), snap("0201", 9)]
    events = detect_openings({}, curr, W_ALL)
    assert sorted(e.snapshot.section_id for e in events) == ["0101", "0201"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_diff.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.diff'`.

- [ ] **Step 3: Write `src/testudo_watch/diff.py`**

```python
from __future__ import annotations

from testudo_watch.models import OpeningEvent, SectionSnapshot, Watch


def detect_openings(
    prev: dict[str, SectionSnapshot],
    curr: list[SectionSnapshot],
    watch: Watch,
) -> list[OpeningEvent]:
    wanted = set(watch.sections)
    events: list[OpeningEvent] = []
    for snapshot in curr:
        if wanted and snapshot.section_id not in wanted:
            continue
        if snapshot.open_seats <= 0:
            continue
        previous = prev.get(snapshot.section_id)
        if previous is None or previous.open_seats == 0:
            events.append(OpeningEvent(snapshot=snapshot))
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_diff.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/diff.py tests/test_diff.py
git commit -m "feat: add open-seat transition detection"
```

---

## Task 7: Notifier interface, console, and Twilio SMS

**Files:**
- Create: `src/testudo_watch/notifier/__init__.py`, `src/testudo_watch/notifier/console.py`, `src/testudo_watch/notifier/sms.py`, `tests/test_notifier.py`

**Interfaces:**
- Consumes: `AppConfig`, `ConfigError` from `testudo_watch.config`.
- Produces:
  - `NotifierError(Exception)`.
  - `Notifier` — `typing.Protocol` with `send(self, message: str) -> None`.
  - `ConsoleNotifier` — logs at WARNING via `logging.getLogger("testudo_watch.notifier")` and prints to stdout; never raises.
  - `TwilioNotifier(account_sid: str, auth_token: str, to_number: str, from_number: str)` plus `TwilioNotifier.from_env() -> TwilioNotifier` (raises `ConfigError` if any of `ACCOUNT_SID`, `AUTH_TOKEN`, `TO_NUMBER`, `FROM_NUMBER` is missing). `send` wraps Twilio exceptions in `NotifierError`.
  - `build_notifier(config: AppConfig) -> Notifier` — returns `ConsoleNotifier` for `"console"`, `TwilioNotifier.from_env()` for `"sms"`.

- [ ] **Step 1: Write the failing test — `tests/test_notifier.py`**

```python
import pytest

from testudo_watch.config import AppConfig, ConfigError
from testudo_watch.notifier import NotifierError, build_notifier
from testudo_watch.notifier.console import ConsoleNotifier
from testudo_watch.notifier.sms import TwilioNotifier


def cfg(notifier):
    return AppConfig(
        poll_interval_seconds=30,
        notifier=notifier,
        db_path="x.db",
        log_dir="logs",
    )


def test_console_notifier_prints_and_does_not_raise(capsys):
    ConsoleNotifier().send("CMSC351 0101 has 3 open seats")
    assert "CMSC351 0101 has 3 open seats" in capsys.readouterr().out


def test_build_notifier_console(capsys):
    assert isinstance(build_notifier(cfg("console")), ConsoleNotifier)


def test_build_notifier_sms_requires_env(monkeypatch):
    for key in ("ACCOUNT_SID", "AUTH_TOKEN", "TO_NUMBER", "FROM_NUMBER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigError):
        build_notifier(cfg("sms"))


def test_twilio_notifier_from_env_builds(monkeypatch):
    monkeypatch.setenv("ACCOUNT_SID", "AC123")
    monkeypatch.setenv("AUTH_TOKEN", "tok")
    monkeypatch.setenv("TO_NUMBER", "+15555550123")
    monkeypatch.setenv("FROM_NUMBER", "+15555550188")
    n = TwilioNotifier.from_env()
    assert n.to_number == "+15555550123"


def test_twilio_notifier_send_wraps_errors(monkeypatch):
    n = TwilioNotifier("AC123", "tok", "+15555550123", "+15555550188")

    class _BoomClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("twilio down")

    monkeypatch.setattr("testudo_watch.notifier.sms.Client", _BoomClient)
    with pytest.raises(NotifierError):
        n.send("hi")


def test_twilio_notifier_send_calls_client(monkeypatch):
    n = TwilioNotifier("AC123", "tok", "+15555550123", "+15555550188")
    captured = {}

    class _OkClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("testudo_watch.notifier.sms.Client", _OkClient)
    n.send("seat open")
    assert captured == {
        "to": "+15555550123",
        "from_": "+15555550188",
        "body": "seat open",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_notifier.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.notifier'`.

- [ ] **Step 3: Write `src/testudo_watch/notifier/__init__.py`**

```python
from __future__ import annotations

from typing import Protocol

from testudo_watch.config import AppConfig


class NotifierError(Exception):
    """Raised when a notification could not be delivered."""


class Notifier(Protocol):
    def send(self, message: str) -> None: ...


def build_notifier(config: AppConfig) -> Notifier:
    if config.notifier == "sms":
        from testudo_watch.notifier.sms import TwilioNotifier

        return TwilioNotifier.from_env()
    from testudo_watch.notifier.console import ConsoleNotifier

    return ConsoleNotifier()
```

- [ ] **Step 4: Write `src/testudo_watch/notifier/console.py`**

```python
from __future__ import annotations

import logging

_log = logging.getLogger("testudo_watch.notifier")


class ConsoleNotifier:
    def send(self, message: str) -> None:
        _log.warning("NOTIFY: %s", message)
        print(message)
```

- [ ] **Step 5: Write `src/testudo_watch/notifier/sms.py`**

```python
from __future__ import annotations

import os

from twilio.rest import Client

from testudo_watch.config import ConfigError
from testudo_watch.notifier import NotifierError

_REQUIRED_ENV = ("ACCOUNT_SID", "AUTH_TOKEN", "TO_NUMBER", "FROM_NUMBER")


class TwilioNotifier:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        to_number: str,
        from_number: str,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.to_number = to_number
        self.from_number = from_number

    @classmethod
    def from_env(cls) -> "TwilioNotifier":
        missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if missing:
            raise ConfigError(
                f"notifier = \"sms\" requires env vars: {', '.join(missing)}"
            )
        return cls(
            account_sid=os.environ["ACCOUNT_SID"],
            auth_token=os.environ["AUTH_TOKEN"],
            to_number=os.environ["TO_NUMBER"],
            from_number=os.environ["FROM_NUMBER"],
        )

    def send(self, message: str) -> None:
        try:
            client = Client(self.account_sid, self.auth_token)
            client.messages.create(
                to=self.to_number, from_=self.from_number, body=message
            )
        except Exception as exc:  # twilio raises many exception types
            raise NotifierError(f"Twilio send failed: {exc}") from exc
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_notifier.py -q`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/testudo_watch/notifier tests/test_notifier.py
git commit -m "feat: add Notifier interface with console and Twilio SMS impls"
```

---

## Task 8: Logging setup

**Files:**
- Create: `src/testudo_watch/logging_setup.py`, `tests/test_logging_setup.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `configure_logging(log_dir: str | pathlib.Path, *, verbose: bool = False) -> None`.
  - Attaches one `StreamHandler` (level `DEBUG` if `verbose` else `INFO`) and one `logging.handlers.RotatingFileHandler` at `<log_dir>/testudo_watch.log` (`maxBytes=1_000_000`, `backupCount=5`, level `INFO`) to the `testudo_watch` logger.
  - Creates `log_dir` if missing. Idempotent: calling twice does not duplicate handlers.

- [ ] **Step 1: Write the failing test — `tests/test_logging_setup.py`**

```python
import logging

from testudo_watch.logging_setup import configure_logging


def test_creates_log_dir_and_file(tmp_path):
    configure_logging(tmp_path / "logs")
    logging.getLogger("testudo_watch").info("hello")
    assert (tmp_path / "logs" / "testudo_watch.log").exists()


def test_is_idempotent(tmp_path):
    configure_logging(tmp_path / "logs")
    configure_logging(tmp_path / "logs")
    handlers = logging.getLogger("testudo_watch").handlers
    assert len(handlers) == 2


def test_verbose_sets_stream_handler_to_debug(tmp_path):
    configure_logging(tmp_path / "logs", verbose=True)
    stream = [
        h
        for h in logging.getLogger("testudo_watch").handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert stream and stream[0].level == logging.DEBUG
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_logging_setup.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/testudo_watch/logging_setup.py`**

```python
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(log_dir: str | Path, *, verbose: bool = False) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("testudo_watch")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:  # already configured
        return

    formatter = logging.Formatter(_FORMAT)

    stream = logging.StreamHandler()
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    file_handler = RotatingFileHandler(
        log_dir / "testudo_watch.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_logging_setup.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/logging_setup.py tests/test_logging_setup.py
git commit -m "feat: add console + rotating-file logging setup"
```

---

## Task 9: Engine (poll loop)

**Files:**
- Create: `src/testudo_watch/engine.py`, `tests/test_engine.py`

**Interfaces:**
- Consumes: `AppConfig` (`testudo_watch.config`), `Database` (`testudo_watch.db`), `Notifier` + `NotifierError` (`testudo_watch.notifier`), `fetch_sections` (`testudo_watch.scraper`), `detect_openings` (`testudo_watch.diff`), `OpeningEvent`/`SectionSnapshot`/`Watch` (`testudo_watch.models`).
- Produces:
  - `FAILURE_ALERT_THRESHOLD = 5`, `POLITE_DELAY_SECONDS = 3`, `JITTER_MAX_SECONDS = 5`.
  - `format_opening(event: OpeningEvent) -> str`.
  - `run(config: AppConfig, db: Database, notifier: Notifier, session, *, once: bool = False, fetch=fetch_sections, sleep=time.sleep, failure_counts: dict[str, int] | None = None) -> None`.
    - Iterates `db.get_active_watches()`.
    - Per watch: `fetch(session, course_id, term_id)` → `db.get_snapshots(watch)` → `detect_openings(...)`. For each event: `notifier.send(format_opening(event))`; on success `db.record_notification(snapshot, channel=config.notifier, status="sent")`; on `NotifierError` `... status="failed", detail=str(exc)` and remember that `section_id` failed. Then `db.upsert_snapshots(...)` for every fetched snapshot **except** sections whose send failed this cycle.
    - On `ScrapeError`/`requests.RequestException`: log at ERROR with `exc_info=True`, `failure_counts[key] += 1` (key = `f"{course_id}/{term_id}"`). When it first reaches `FAILURE_ALERT_THRESHOLD`, `notifier.send("testudo-watch: <course> has failed N consecutive checks")` and `db.record_notification` a synthetic snapshot (`SectionSnapshot(course, term, "-", 0, 0, 0)`, `status="health"`). A later success resets the count to 0.
    - Sleep `POLITE_DELAY_SECONDS` between watches.
    - If `once`: return after one pass. Otherwise sleep `config.poll_interval_seconds + random.uniform(0, JITTER_MAX_SECONDS)` and loop, checking a module-level stop flag set by SIGINT/SIGTERM handlers (installed only when `once` is False, restored on return).

- [ ] **Step 1: Write the failing test — `tests/test_engine.py`**

```python
import pytest

from testudo_watch.db import Database
from testudo_watch.engine import format_opening, run
from testudo_watch.config import AppConfig
from testudo_watch.models import OpeningEvent, SectionSnapshot, Watch
from testudo_watch.notifier import NotifierError
from testudo_watch.scraper import ScrapeError


def cfg():
    return AppConfig(
        poll_interval_seconds=30, notifier="console", db_path="x", log_dir="l"
    )


class FakeNotifier:
    def __init__(self, fail_on=()):
        self.sent = []
        self.fail_on = set(fail_on)

    def send(self, message):
        self.sent.append(message)
        for token in self.fail_on:
            if token in message:
                raise NotifierError("nope")


def snap(course, section, open_seats):
    return SectionSnapshot(course, "202601", section, 100, open_seats, 0)


def test_format_opening_names_course_section_and_seats():
    msg = format_opening(OpeningEvent(snap("CMSC351", "0101", 4)))
    assert "CMSC351" in msg and "0101" in msg and "4" in msg


def test_run_once_notifies_and_persists(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()

    def fake_fetch(session, course_id, term_id):
        return [snap(course_id, "0101", 6), snap(course_id, "0201", 0)]

    run(cfg(), db, notifier, session=None, once=True, fetch=fake_fetch,
        sleep=lambda s: None)

    assert len(notifier.sent) == 1 and "0101" in notifier.sent[0]
    assert db.get_snapshots(Watch("CMSC351", "202601", ()))["0101"].open_seats == 6
    rows = db.connection.execute("SELECT status FROM notifications").fetchall()
    assert [r["status"] for r in rows] == ["sent"]
    db.close()


def test_run_once_second_pass_is_silent_when_seats_unchanged(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()
    fetch = lambda s, c, t: [snap(c, "0101", 6)]
    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    assert len(notifier.sent) == 1
    db.close()


def test_failed_send_leaves_snapshot_stale_so_it_retries(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier(fail_on=["0101"])
    fetch = lambda s, c, t: [snap(c, "0101", 6)]

    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    # snapshot NOT written (still no prior), so a second pass notifies again
    assert db.get_snapshots(Watch("CMSC351", "202601", ())) == {}
    run(cfg(), db, notifier, None, once=True, fetch=fetch, sleep=lambda s: None)
    assert len(notifier.sent) == 2
    rows = db.connection.execute("SELECT status FROM notifications").fetchall()
    assert [r["status"] for r in rows] == ["failed", "failed"]
    db.close()


def test_scrape_error_is_swallowed_and_counted(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()
    counts = {}

    def boom(session, course_id, term_id):
        raise ScrapeError("testudo down")

    run(cfg(), db, notifier, None, once=True, fetch=boom, sleep=lambda s: None,
        failure_counts=counts)
    assert counts["CMSC351/202601"] == 1
    assert notifier.sent == []
    db.close()


def test_health_alert_fires_once_at_threshold(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ())])
    notifier = FakeNotifier()
    counts = {}

    def boom(session, course_id, term_id):
        raise ScrapeError("down")

    for _ in range(7):
        run(cfg(), db, notifier, None, once=True, fetch=boom,
            sleep=lambda s: None, failure_counts=counts)

    health = [m for m in notifier.sent if "consecutive" in m]
    assert len(health) == 1
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_engine.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'testudo_watch.engine'`.

- [ ] **Step 3: Write `src/testudo_watch/engine.py`**

```python
from __future__ import annotations

import logging
import random
import signal
import time
from collections.abc import Callable

import requests

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.diff import detect_openings
from testudo_watch.models import OpeningEvent, SectionSnapshot
from testudo_watch.notifier import Notifier, NotifierError
from testudo_watch.scraper import ScrapeError, fetch_sections

_log = logging.getLogger("testudo_watch.engine")

FAILURE_ALERT_THRESHOLD = 5
POLITE_DELAY_SECONDS = 3
JITTER_MAX_SECONDS = 5

_stop = False


def _request_stop(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    _log.info("stop signal received; finishing current cycle")


def format_opening(event: OpeningEvent) -> str:
    s = event.snapshot
    return (
        f"{s.course_id} section {s.section_id} ({s.term_id}) has "
        f"{s.open_seats} open seat(s) [total {s.total_seats}]"
    )


def _process_watch(config, db, notifier, session, watch, fetch, failure_counts) -> None:
    key = f"{watch.course_id}/{watch.term_id}"
    try:
        current = fetch(session, watch.course_id, watch.term_id)
    except (ScrapeError, requests.RequestException):
        _log.error("scrape failed for %s", key, exc_info=True)
        failure_counts[key] = failure_counts.get(key, 0) + 1
        if failure_counts[key] == FAILURE_ALERT_THRESHOLD:
            msg = (
                f"testudo-watch: {watch.course_id} has failed "
                f"{FAILURE_ALERT_THRESHOLD} consecutive checks"
            )
            try:
                notifier.send(msg)
            except NotifierError:
                _log.error("health alert send failed", exc_info=True)
            db.record_notification(
                SectionSnapshot(watch.course_id, watch.term_id, "-", 0, 0, 0),
                channel=config.notifier,
                status="health",
            )
        return

    failure_counts[key] = 0
    previous = db.get_snapshots(watch)
    events = detect_openings(previous, current, watch)

    failed_sections: set[str] = set()
    for event in events:
        message = format_opening(event)
        _log.warning("opening detected: %s", message)
        try:
            notifier.send(message)
        except NotifierError as exc:
            _log.error("notify failed for %s", message, exc_info=True)
            failed_sections.add(event.snapshot.section_id)
            db.record_notification(
                event.snapshot,
                channel=config.notifier,
                status="failed",
                detail=str(exc),
            )
        else:
            db.record_notification(
                event.snapshot, channel=config.notifier, status="sent"
            )

    to_persist = [s for s in current if s.section_id not in failed_sections]
    db.upsert_snapshots(to_persist)
    _log.info(
        "%s: %s",
        key,
        ", ".join(f"{s.section_id}={s.open_seats}" for s in current) or "no sections",
    )


def run(
    config: AppConfig,
    db: Database,
    notifier: Notifier,
    session: requests.Session | None,
    *,
    once: bool = False,
    fetch: Callable = fetch_sections,
    sleep: Callable[[float], None] = time.sleep,
    failure_counts: dict[str, int] | None = None,
) -> None:
    global _stop
    if failure_counts is None:
        failure_counts = {}

    previous_handlers = {}
    if not once:
        _stop = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _request_stop)

    try:
        while True:
            for watch in db.get_active_watches():
                if _stop:
                    return
                _process_watch(
                    config, db, notifier, session, watch, fetch, failure_counts
                )
                if not once:
                    sleep(POLITE_DELAY_SECONDS)
            if once or _stop:
                return
            sleep(config.poll_interval_seconds + random.uniform(0, JITTER_MAX_SECONDS))
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/testudo_watch/engine.py tests/test_engine.py
git commit -m "feat: add poll-loop engine with failure handling"
```

---

## Task 10: CLI, README, and removal of the old script

**Files:**
- Create: `src/testudo_watch/cli.py`, `tests/test_cli.py`
- Modify: `README.md`
- Delete: `send_sms.py`

**Interfaces:**
- Consumes: `load_config`/`ConfigError` (`testudo_watch.config`), `configure_logging` (`testudo_watch.logging_setup`), `Database` (`testudo_watch.db`), `build_notifier` (`testudo_watch.notifier`), `build_session` (`testudo_watch.scraper`), `run` (`testudo_watch.engine`).
- Produces: `main(argv: list[str] | None = None) -> int`.
  - `testudo-watch run [--config PATH] [--db PATH] [--verbose]` — full loop.
  - `testudo-watch check-once [--config PATH] [--db PATH] [--verbose]` — one pass (`run(..., once=True)`).
  - `testudo-watch list [--config PATH] [--db PATH]` — print each active watch and its last-seen per-section open counts.
  - Exit codes: `0` normal/clean shutdown, `1` `ConfigError`, `2` unexpected fatal exception.
  - `--config` default `watches.toml`; `--db` overrides `AppConfig.db_path`.

- [ ] **Step 1: Write the failing test — `tests/test_cli.py`**

```python
import textwrap

import pytest

from testudo_watch import cli
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot


def write_config(tmp_path, notifier="console"):
    p = tmp_path / "watches.toml"
    p.write_text(
        textwrap.dedent(
            f"""
            poll_interval_seconds = 30
            notifier = "{notifier}"
            [[watch]]
            course_id = "CMSC351"
            term_id = "202601"
            sections = ["0101"]
            """
        ),
        encoding="utf-8",
    )
    return p


def test_main_returns_1_on_config_error(tmp_path, capsys):
    missing = tmp_path / "nope.toml"
    rc = cli.main(["run", "--config", str(missing)])
    assert rc == 1
    assert "config" in capsys.readouterr().err.lower()


def test_check_once_runs_one_pass(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    db_path = tmp_path / "state.db"

    calls = {}

    def fake_fetch(session, course_id, term_id):
        calls["hit"] = (course_id, term_id)
        return [SectionSnapshot(course_id, term_id, "0101", 100, 5, 0)]

    # The CLI forwards its own fetch_sections reference into engine.run,
    # so patching it here is what takes effect.
    monkeypatch.setattr("testudo_watch.cli.fetch_sections", fake_fetch)

    rc = cli.main(
        ["check-once", "--config", str(config_path), "--db", str(db_path)]
    )
    assert rc == 0
    assert calls["hit"] == ("CMSC351", "202601")

    from testudo_watch.models import Watch

    db = Database(db_path)
    snaps = db.get_snapshots(Watch("CMSC351", "202601", ("0101",)))
    assert snaps["0101"].open_seats == 5
    db.close()


def test_list_prints_watches_and_last_seen(tmp_path, capsys):
    config_path = write_config(tmp_path)
    db_path = tmp_path / "state.db"
    db = Database(db_path)
    from testudo_watch.models import Watch

    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.upsert_snapshots([SectionSnapshot("CMSC351", "202601", "0101", 100, 2, 0)])
    db.close()

    rc = cli.main(["list", "--config", str(config_path), "--db", str(db_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CMSC351" in out and "0101" in out and "2" in out
```

Note: `check-once` must pass the CLI-selected `fetch` into `engine.run`. Implement the CLI so it imports `fetch_sections` from `testudo_watch.scraper` and forwards it as `run(..., fetch=fetch_sections)`, which makes the monkeypatch on `testudo_watch.cli.fetch_sections` effective.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -q`
Expected: FAIL with `ImportError`/`AttributeError` (no `cli` module).

- [ ] **Step 3: Write `src/testudo_watch/cli.py`**

```python
from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from testudo_watch.config import ConfigError, load_config
from testudo_watch.db import Database
from testudo_watch.engine import run
from testudo_watch.logging_setup import configure_logging
from testudo_watch.notifier import build_notifier
from testudo_watch.scraper import build_session, fetch_sections

_log = logging.getLogger("testudo_watch.cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testudo-watch")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "check-once", "list"):
        p = sub.add_parser(name)
        p.add_argument("--config", default="watches.toml")
        p.add_argument("--db", default=None)
        if name != "list":
            p.add_argument("--verbose", action="store_true")
    return parser


def _load(args):
    config, watches = load_config(args.config)
    if args.db:
        config = type(config)(
            poll_interval_seconds=config.poll_interval_seconds,
            notifier=config.notifier,
            db_path=args.db,
            log_dir=config.log_dir,
        )
    return config, watches


def _cmd_list(config, watches) -> int:
    db = Database(config.db_path)
    try:
        db.sync_watches(watches)
        for watch in db.get_active_watches():
            snaps = db.get_snapshots(watch)
            targets = watch.sections or tuple(sorted(snaps))
            print(f"{watch.course_id} ({watch.term_id})")
            if not targets:
                print("  (no sections seen yet)")
            for section_id in targets:
                snap = snaps.get(section_id)
                seen = (
                    f"open={snap.open_seats} total={snap.total_seats} "
                    f"waitlist={snap.waitlist}"
                    if snap
                    else "not seen yet"
                )
                print(f"  {section_id}: {seen}")
    finally:
        db.close()
    return 0


def _cmd_run(config, watches, *, once: bool) -> int:
    db = Database(config.db_path)
    session = build_session()
    try:
        db.sync_watches(watches)
        notifier = build_notifier(config)
        run(config, db, notifier, session, once=once, fetch=fetch_sections)
    finally:
        session.close()
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _build_parser().parse_args(argv)
    try:
        config, watches = _load(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if args.command == "list":
        configure_logging(config.log_dir)
        return _cmd_list(config, watches)

    configure_logging(config.log_dir, verbose=getattr(args, "verbose", False))
    try:
        return _cmd_run(config, watches, once=args.command == "check-once")
    except ConfigError as exc:  # e.g. missing Twilio env from build_notifier
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001
        _log.exception("fatal error")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -q`
Expected: 3 passed.

- [ ] **Step 5: Delete the old script**

```bash
git rm send_sms.py
```

- [ ] **Step 6: Rewrite `README.md`**

```markdown
# testudo-watch

Watches the UMD Testudo Schedule of Classes for open seats in the course
sections you care about, and notifies you (console log or Twilio SMS) the
moment a watched section goes from 0 open seats to 1 or more.

## Install

Requires Python 3.11+.

```bash
python -m pip install -e ".[dev]"      # or: pip install -r requirements.txt
```

## Configure

Edit `watches.toml`:

```toml
poll_interval_seconds = 30      # minimum 15
notifier = "console"            # "console" or "sms"

[[watch]]
course_id = "CMSC351"
term_id = "202601"              # set to the current Testudo term
sections = ["0101", "0201"]     # empty = notify if ANY section opens
```

For SMS, copy `.env.example` to `.env` and fill in your Twilio
`ACCOUNT_SID`, `AUTH_TOKEN`, `TO_NUMBER`, and `FROM_NUMBER`, then set
`notifier = "sms"`.

## Use

```bash
testudo-watch check-once   # one pass, prints parsed seat counts
testudo-watch run          # poll forever; Ctrl-C to stop
testudo-watch list         # show watches and last-seen seat counts
```

State is kept in `testudo_watch.db` (SQLite); logs in `logs/testudo_watch.log`.
Stopping and restarting does not re-notify for a section that is still open.

## Test

```bash
python -m pytest
```
```

- [ ] **Step 7: Run the full test suite**

Run: `python -m pytest -q`
Expected: all tests pass (about 40).

- [ ] **Step 8: Live smoke test (manual, network required)**

Set a real current `term_id` in `watches.toml` for a known course, then:
```bash
testudo-watch check-once --verbose
```
Expected: logs one `CMSC351/<term>: 0101=<n>, ...` line with real seat counts and no traceback. If parsing fails because UMD changed their markup, adjust the selectors in `scraper.py` (`div.section`, `input[name=sectionId]`, `span.total-seats-count`, `span.open-seats-count`, `span.waitlist-count`) and re-run `python -m pytest tests/test_scraper.py`.

- [ ] **Step 9: Commit**

```bash
git add src/testudo_watch/cli.py tests/test_cli.py README.md
git commit -m "feat: add CLI, rewrite README, remove legacy send_sms.py"
```

---

## Self-Review Notes

**Spec coverage:**
- Package layout, dependencies, Python floor → Task 1.
- `watches.toml` schema + validation + interval floor → Task 3 (example file in Task 1).
- SQLite tables (`watches`, `section_snapshots`, `notifications`), `sync_watches` deactivation, snapshot upsert, notification log → Task 4.
- Scraper against Testudo's real markup, `User-Agent`, timeout, non-200 → `ScrapeError` → Task 5. (Design said `_openSectionsOnly` off; the plan uses the dedicated `/soc/<term>/sections` endpoint discovered during planning, which returns all sections unfiltered — same effect, lighter payload.)
- `0 -> >0` trigger, first-sighting rule, state-based de-dupe, section filtering → Task 6.
- Notifier Protocol + `ConsoleNotifier` + `TwilioNotifier` + factory; missing Twilio env → `ConfigError` → Task 7.
- stdlib logging, console + rotating file, `--verbose` → Task 8.
- Engine loop: per-watch try/except, loop never dies, per-watch failure counter, single health alert at threshold, snapshot-skip on failed send, polite delay, jitter, SIGINT/SIGTERM clean exit, `once` mode → Task 9.
- CLI `run` / `check-once` / `list`, exit codes 0/1/2, `.env` load → Task 10.
- `send_sms.py` deleted, README rewritten → Task 10.
- Testing strategy (fixtures, diff table, temp DB, fake notifier/scraper) → Tasks 5, 6, 4, 9.

**Deviations from spec (intentional):**
- The spec's `recent_open_episode_notified` DB helper is omitted: de-dupe is fully achieved by the snapshot-state gate in `detect_openings` plus not persisting a snapshot when its notification failed. The `notifications` table remains as an audit log. No behavior lost.
- Scraper uses `https://app.testudo.umd.edu/soc/<term>/sections?courseIds=<course>` instead of the full search URL — verified during planning to return the section blocks with `total-seats-count` / `open-seats-count` / `waitlist-count` spans.

**Type consistency:** `Watch`, `SectionSnapshot`, `OpeningEvent` field names are used identically across Tasks 2/4/5/6/9. `Database` method names (`sync_watches`, `get_active_watches`, `get_snapshots`, `upsert_snapshots`, `record_notification`, `connection`) match between Tasks 4, 9, 10. `run(...)` keyword params (`once`, `fetch`, `sleep`, `failure_counts`) match between Tasks 9 and 10. `configure_logging(log_dir, *, verbose)` matches between Tasks 8 and 10. `build_notifier(config)` matches between Tasks 7 and 10.
