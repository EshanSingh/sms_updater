# Testudo Watch — Phase 3b Implementation Plan (Notification History)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/notifications` page to `testudo-watch serve` — the full notification history, filterable by course and date range, page-number paginated.

**Architecture:** A DB method (`query_notifications`) adds filtering/pagination on top of the existing `notifications` table — no schema change. A pure view builder (`build_history_view`) turns a page of rows into a `HistoryView`, mirroring the Phase 2/3a builder pattern. A `GET /notifications` route wires it to a new plain (non-HTMX) template, since a paginated history browse has no live-refreshing state to preserve.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, stdlib `sqlite3` / `math`. Tests: pytest + `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-09-11-testudo-watch-phase3b-design.md`

## Global Constraints

- Python 3.11+. No new dependencies.
- All source under `src/testudo_watch/`; templates under `src/testudo_watch/templates/`; tests under `tests/`.
- No schema change, no `SCHEMA_VERSION` bump, no new write path. `/notifications` is read-only.
- Filters are **course and date range only** (no status/channel filter — explicitly out of scope).
- Pagination is page-number, driven by GET query-string params (`course`, `since`, `until`, `page`), plain `<a>` links — no HTMX on this page.
- `page`, `since`, `until` are all user-editable URL values: malformed input must degrade gracefully (treated as absent/default), never a 500. `page` clamps to `>= 1`.
- `GET /notifications` uses the same `_open_db(config)` guidance-page pattern as the existing `/`, `/fragments/*`, and `/watches` routes — a corrupt/newer-schema/unreadable DB renders `no_data.html` at HTTP 200, never a 500.
- Test suite stays at 0 warnings. Every code step is TDD.

---

## File Structure

**Created:**
- `src/testudo_watch/templates/notifications.html` — full history page: nav, filter form, results table, pager
- `tests/test_web_history.py` — `TestClient` tests for `GET /notifications`

**Modified:**
- `src/testudo_watch/db.py` — `NotificationPage` dataclass; `query_notifications(...)`; `notification_course_ids()`
- `src/testudo_watch/web.py` — `import math`; `HistoryView` dataclass; `build_history_view(...)`; `GET /notifications` route
- `src/testudo_watch/templates/_notifications.html` — add a "View full history" link
- `src/testudo_watch/templates/dashboard.html` — nav gains "Notification history"
- `src/testudo_watch/templates/manage.html` — nav gains "Notification history"
- `tests/test_db.py` — `query_notifications` filtering/pagination; `notification_course_ids`
- `tests/test_web_views.py` — `build_history_view` unit tests
- `README.md` — document `/notifications`

**Deleted:** none.

---

## Task 1: DB — `query_notifications` + `notification_course_ids`

**Files:**
- Modify: `src/testudo_watch/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `NotificationRow` (existing dataclass), the existing `notifications` table.
- Produces:
  - `NotificationPage(rows: list[NotificationRow], total: int)` — frozen dataclass.
  - `query_notifications(self, *, course_id: str | None = None, since: str | None = None, until: str | None = None, page: int = 1, page_size: int = 50) -> NotificationPage`.
  - `notification_course_ids(self) -> list[str]` — distinct course ids that have ever appeared in `notifications`, sorted.

- [ ] **Step 1: Write the failing tests — append to `tests/test_db.py`**

Add `NotificationPage` to the existing `from testudo_watch.db import (...)` line. Append:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_db.py -q -k "query_notifications or notification_course_ids"`
Expected: `ImportError: cannot import name 'NotificationPage'` / `AttributeError` on the missing methods.

- [ ] **Step 3: Add `NotificationPage` and the two methods to `src/testudo_watch/db.py`**

Add the dataclass immediately after `NotificationRow`:

```python
@dataclass(frozen=True)
class NotificationPage:
    rows: list[NotificationRow]
    total: int
```

Add these two methods to `Database`, immediately after `recent_notifications`:

```python
    def query_notifications(
        self,
        *,
        course_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> NotificationPage:
        clauses: list[str] = []
        params: list[str] = []
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        if since:
            clauses.append("sent_at >= ?")
            params.append(f"{since} 00:00:00")
        if until:
            clauses.append("sent_at <= ?")
            params.append(f"{until} 23:59:59")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        total = self.connection.execute(
            f"SELECT COUNT(*) FROM notifications {where}", params
        ).fetchone()[0]

        rows = self.connection.execute(
            f"SELECT sent_at, course_id, term_id, section_id, open_seats, "
            f"channel, status, detail FROM notifications {where} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()

        return NotificationPage(
            rows=[
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
            ],
            total=total,
        )

    def notification_course_ids(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT course_id FROM notifications ORDER BY course_id"
        ).fetchall()
        return [r["course_id"] for r in rows]
```

- [ ] **Step 4: Run the db tests**

Run: `python -m pytest tests/test_db.py -q`
Expected: all pass, including every pre-existing `test_db.py` test.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 6: Commit**

```bash
git add src/testudo_watch/db.py tests/test_db.py
git commit -m "feat(db): add filtered/paginated notification query + course list"
```

---

## Task 2: web.py — `HistoryView` + `build_history_view` (pure builder)

**Files:**
- Modify: `src/testudo_watch/web.py`
- Test: `tests/test_web_views.py`

**Interfaces:**
- Consumes: `query_notifications`, `notification_course_ids` (Task 1); `NotificationView`, `humanize_age`, `parse_db_utc` (existing).
- Produces:
  - `HistoryView(rows: list[NotificationView], courses: list[str], course_id: str | None, since: str | None, until: str | None, page: int, total_pages: int, has_prev: bool, has_next: bool)` — frozen.
  - `build_history_view(db: Database, *, course_id: str | None, since: str | None, until: str | None, page: int, page_size: int = 50, now: datetime) -> HistoryView`.

- [ ] **Step 1: Write the failing tests — append to `tests/test_web_views.py`**

Add `from testudo_watch.web import build_history_view` (and `HistoryView` if you want to assert `isinstance`) to the imports. Append:

```python
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


def test_build_history_view_basic_shape(tmp_path):
    from testudo_watch.web import HistoryView

    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("CMSC351", "0101", 4, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 6, "2026-09-02 10:00:00"),
        ],
    )
    view = build_history_view(
        db,
        course_id=None,
        since=None,
        until=None,
        page=1,
        now=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert isinstance(view, HistoryView)
    assert [r.course_id for r in view.rows] == ["MATH240", "CMSC351"]
    assert view.courses == ["CMSC351", "MATH240"]
    assert view.page == 1
    assert view.total_pages == 1
    assert view.has_prev is False and view.has_next is False
    db.close()


def test_build_history_view_pagination_flags(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(
        db,
        [
            ("CMSC351", f"0{i:03d}", 1, f"2026-09-01 10:{i:02d}:00")
            for i in range(5)
        ],
    )
    view = build_history_view(
        db,
        course_id=None,
        since=None,
        until=None,
        page=2,
        page_size=2,
        now=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert view.total_pages == 3
    assert view.has_prev is True and view.has_next is True
    assert len(view.rows) == 2
    db.close()


def test_build_history_view_empty_db_has_single_page_no_prev_next(tmp_path):
    db = Database(tmp_path / "s.db")
    view = build_history_view(
        db,
        course_id=None,
        since=None,
        until=None,
        page=1,
        now=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert view.rows == [] and view.total_pages == 1
    assert view.has_prev is False and view.has_next is False
    db.close()


def test_build_history_view_echoes_filters_for_form_prefill(tmp_path):
    db = Database(tmp_path / "s.db")
    _seed_notifications(db, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    view = build_history_view(
        db,
        course_id="CMSC351",
        since="2026-09-01",
        until="2026-09-10",
        page=1,
        now=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert (view.course_id, view.since, view.until) == (
        "CMSC351",
        "2026-09-01",
        "2026-09-10",
    )
    db.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_web_views.py -q -k "build_history_view"`
Expected: `ImportError: cannot import name 'build_history_view'`.

- [ ] **Step 3: Add `math` import, `HistoryView`, and `build_history_view` to `src/testudo_watch/web.py`**

Add `import math` to the import block (alphabetically, after `import logging`).

Add the dataclass and function immediately after `build_notifications_view`:

```python
@dataclass(frozen=True)
class HistoryView:
    rows: list[NotificationView]
    courses: list[str]
    course_id: str | None
    since: str | None
    until: str | None
    page: int
    total_pages: int
    has_prev: bool
    has_next: bool


def build_history_view(
    db: Database,
    *,
    course_id: str | None,
    since: str | None,
    until: str | None,
    page: int,
    page_size: int = 50,
    now: datetime,
) -> HistoryView:
    result = db.query_notifications(
        course_id=course_id, since=since, until=until, page=page, page_size=page_size
    )
    rows = [
        NotificationView(
            sent_age=humanize_age(parse_db_utc(n.sent_at), now),
            course_id=n.course_id,
            section_id=n.section_id,
            open_seats=n.open_seats,
            channel=n.channel,
            status=n.status,
            detail=n.detail,
        )
        for n in result.rows
    ]
    total_pages = max(1, math.ceil(result.total / page_size))
    return HistoryView(
        rows=rows,
        courses=db.notification_course_ids(),
        course_id=course_id,
        since=since,
        until=until,
        page=page,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
    )
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_web_views.py -q`
Expected: all pass, including every pre-existing test in that file.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 6: Commit**

```bash
git add src/testudo_watch/web.py tests/test_web_views.py
git commit -m "feat(web): add HistoryView + build_history_view for notification history"
```

---

## Task 3: `GET /notifications` route, template, nav, dashboard link, README

**Files:**
- Modify: `src/testudo_watch/web.py`, `src/testudo_watch/templates/_notifications.html`, `src/testudo_watch/templates/dashboard.html`, `src/testudo_watch/templates/manage.html`, `README.md`
- Create: `src/testudo_watch/templates/notifications.html`
- Test: `tests/test_web_history.py`

**Interfaces:**
- Consumes: `build_history_view` (Task 2); `_open_db`, `_TEMPLATES` (existing).
- Produces: `GET /notifications` → renders `notifications.html`, HTTP 200 always (guidance page on a DB error).

- [ ] **Step 1: Write the failing tests — `tests/test_web_history.py`**

```python
from fastapi.testclient import TestClient

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import Watch
from testudo_watch.web import create_app


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path=str(db_path),
        log_dir="logs",
    )


def _seed_notifications(db, entries):
    for course_id, section_id, open_seats, sent_at in entries:
        db.connection.execute(
            "INSERT INTO notifications "
            "(course_id, term_id, section_id, open_seats, sent_at, channel, status, detail) "
            "VALUES (?, '202601', ?, ?, ?, 'console', 'sent', '')",
            (course_id, section_id, open_seats, sent_at),
        )
    db.connection.commit()


def client(tmp_path, entries):
    db = Database(tmp_path / "s.db")
    _seed_notifications(db, entries)
    db.close()
    return TestClient(create_app(cfg(tmp_path / "s.db"), []))


def test_notifications_page_no_filters_lists_everything(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 2, "2026-09-02 10:00:00"),
        ],
    )
    r = c.get("/notifications")
    assert r.status_code == 200
    assert "CMSC351" in r.text and "MATH240" in r.text
    assert 'name="course"' in r.text  # filter form present


def test_notifications_page_filters_by_course(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 2, "2026-09-02 10:00:00"),
        ],
    )
    r = c.get("/notifications?course=CMSC351")
    assert r.status_code == 200
    assert "CMSC351" in r.text and "MATH240" not in r.text


def test_notifications_page_filters_by_date_range(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("CMSC351", "0201", 1, "2026-09-20 10:00:00"),
        ],
    )
    r = c.get("/notifications?since=2026-09-01&until=2026-09-10")
    assert "0101" in r.text and "0201" not in r.text


def test_notifications_page_paginates_with_working_links(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", f"0{i:03d}", 1, f"2026-09-01 10:{i:02d}:00")
            for i in range(60)
        ],
    )
    page1 = c.get("/notifications")
    assert 'href="?course=&since=&until=&page=2"' in page1.text
    assert "Prev" not in page1.text

    page2 = c.get("/notifications?page=2")
    assert "Prev" in page2.text
    assert "Next" not in page2.text  # 60 rows / 50 per page = exactly 2 pages


def test_notifications_page_malformed_page_param_defaults_to_one(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    for bad in ("abc", "0", "-5"):
        r = c.get(f"/notifications?page={bad}")
        assert r.status_code == 200
        assert "CMSC351" in r.text


def test_notifications_page_malformed_date_is_ignored(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/notifications?since=not-a-date")
    assert r.status_code == 200
    assert "CMSC351" in r.text  # filter ignored, row still shown


def test_notifications_page_empty_db_shows_empty_state(tmp_path):
    c = client(tmp_path, [])
    r = c.get("/notifications")
    assert r.status_code == 200
    assert "No notifications yet." in r.text


def test_dashboard_and_manage_and_history_pages_cross_link(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    for path in ("/", "/watches", "/notifications"):
        r = c.get(path)
        assert 'href="/notifications"' in r.text


def test_dashboard_fragment_links_to_full_history(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/fragments/notifications")
    assert 'href="/notifications"' in r.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_web_history.py -q`
Expected: 404 on `/notifications`.

- [ ] **Step 3: Create `src/testudo_watch/templates/notifications.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>testudo-watch — notification history</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 2rem auto; padding: 0 1rem; }
  nav a { margin-right: 1rem; }
  table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
  td, th { text-align: left; padding: .25rem .5rem; border-bottom: 1px solid #eee; }
  .muted { color: #666; font-size: .85em; }
  .badge { font-size: .8em; padding: .1rem .4rem; border-radius: 3px; background: #eee; }
  .badge.sent { background: #d7efdc; }
  .badge.failed, .badge.health-failed { background: #f6cccc; }
  form.filters input, form.filters select { margin-right: .5rem; }
  .pager a { margin-right: 1rem; }
</style>
</head>
<body>
<h1>testudo-watch</h1>
<nav>
  <a href="/">Dashboard</a>
  <a href="/watches">Manage watches</a>
  <a href="/notifications">Notification history</a>
</nav>
<h2>Notification history</h2>

<form class="filters" method="get">
  <select name="course">
    <option value="">All courses</option>
    {% for c in view.courses %}
    <option value="{{ c }}"{% if c == view.course_id %} selected{% endif %}>{{ c }}</option>
    {% endfor %}
  </select>
  <input type="date" name="since" value="{{ view.since or '' }}">
  <input type="date" name="until" value="{{ view.until or '' }}">
  <button type="submit">Filter</button>
  <a href="/notifications">Clear filters</a>
</form>

{% if not view.rows %}
<p class="muted">No notifications yet.</p>
{% else %}
<table>
  {% for n in view.rows %}
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

<p class="pager muted">
  Page {{ view.page }} of {{ view.total_pages }}
  {% if view.has_prev %}
  <a href="?course={{ view.course_id or '' }}&since={{ view.since or '' }}&until={{ view.until or '' }}&page={{ view.page - 1 }}">&laquo; Prev</a>
  {% endif %}
  {% if view.has_next %}
  <a href="?course={{ view.course_id or '' }}&since={{ view.since or '' }}&until={{ view.until or '' }}&page={{ view.page + 1 }}">Next &raquo;</a>
  {% endif %}
</p>
</body>
</html>
```

- [ ] **Step 4: Add the route to `web.py`**

Inside `create_app`, before `return app`:

```python
    @app.get("/notifications", response_class=HTMLResponse)
    def notifications_page(request: Request):
        course_id = request.query_params.get("course") or None
        since = request.query_params.get("since") or None
        until = request.query_params.get("until") or None
        if since and not re.match(r"^\d{4}-\d{2}-\d{2}$", since):
            since = None
        if until and not re.match(r"^\d{4}-\d{2}-\d{2}$", until):
            until = None
        try:
            page = int(request.query_params.get("page", "1"))
        except ValueError:
            page = 1
        if page < 1:
            page = 1
        with _open_db(config) as db:
            if db is None:
                return _TEMPLATES.TemplateResponse(request, "no_data.html", {})
            view = build_history_view(
                db,
                course_id=course_id,
                since=since,
                until=until,
                page=page,
                now=datetime.now(timezone.utc),
            )
        return _TEMPLATES.TemplateResponse(
            request, "notifications.html", {"view": view}
        )
```

- [ ] **Step 5: Update `_notifications.html`, `dashboard.html`, `manage.html`**

In `src/testudo_watch/templates/_notifications.html`, add one line after the closing `{% endif %}` (i.e. at the end of the file):

```html
<p class="muted"><a href="/notifications">View full history &rarr;</a></p>
```

In `src/testudo_watch/templates/dashboard.html`, change:
```html
<nav style="margin-bottom:.5rem"><a href="/">Dashboard</a> · <a href="/watches">Manage watches</a></nav>
```
to:
```html
<nav style="margin-bottom:.5rem"><a href="/">Dashboard</a> · <a href="/watches">Manage watches</a> · <a href="/notifications">Notification history</a></nav>
```

In `src/testudo_watch/templates/manage.html`, change:
```html
<nav><a href="/">Dashboard</a><a href="/watches">Manage watches</a></nav>
```
to:
```html
<nav><a href="/">Dashboard</a><a href="/watches">Manage watches</a><a href="/notifications">Notification history</a></nav>
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_web_history.py tests/test_web.py tests/test_web_manage.py -q`
Expected: all pass (the dashboard/manage nav tests and the fragment test must still pass with the new links present).

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: green, 0 warnings.

- [ ] **Step 8: Document `/notifications` in `README.md`**

Under the existing "Managing watches from the browser" section, add:

```markdown
### Browsing notification history

`http://127.0.0.1:8477/notifications` lists every notification ever sent,
filterable by course and date range, 50 per page. The dashboard's notification
panel only shows the most recent 50 — use this page to look further back or
narrow down to one course.
```

- [ ] **Step 9: Commit**

```bash
git add src/testudo_watch/web.py src/testudo_watch/templates/notifications.html src/testudo_watch/templates/_notifications.html src/testudo_watch/templates/dashboard.html src/testudo_watch/templates/manage.html tests/test_web_history.py README.md
git commit -m "feat(web): add GET /notifications page with filtering and pagination"
```

---

## Self-Review Notes

**Spec coverage:**
- `NotificationPage`, `query_notifications` (course/date filters, pagination), `notification_course_ids` → Task 1.
- `HistoryView`, `build_history_view` (total_pages, has_prev/has_next, filter echo for form pre-fill) → Task 2.
- `GET /notifications` route, graceful `page`/date degradation, `_open_db` guidance-page pattern → Task 3.
- `notifications.html` template (filter form, table, pager, nav) → Task 3.
- Dashboard fragment link + nav cross-links on all three pages → Task 3.
- README documentation → Task 3.
- Definition of done's "paging preserves filters" → covered by the pager links embedding `course`/`since`/`until` and by `test_notifications_page_paginates_with_working_links`.

**Placeholder scan:** none. Every step carries literal code, SQL, or template HTML.

**Type consistency:** `NotificationPage.rows`/`total` (Task 1) match what `build_history_view` (Task 2) consumes (`result.rows`, `result.total`). `HistoryView`'s field names (`rows, courses, course_id, since, until, page, total_pages, has_prev, has_next`) match exactly what `notifications.html` (Task 3) reads via `view.*`. `build_history_view`'s keyword signature (`course_id, since, until, page, page_size, now`) matches the route's call in Task 3. `query_notifications`'s keyword signature matches between Task 1's definition and Task 2's call site.

**Note on the pager href test:** `test_notifications_page_paginates_with_working_links` asserts the literal query string `?course=&since=&until=&page=2`. Jinja2 only escapes the output of `{{ }}` expressions, not the literal `&` characters an author writes directly in the template source between them — so the `&` separators in the pager's `<a href="...">` (written as plain text in `notifications.html`, not inside an expression) render unescaped exactly as written. This was confirmed rather than assumed; no template/test mismatch is expected.
