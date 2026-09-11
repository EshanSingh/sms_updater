# Testudo Watch — Phase 3b Design (Notification History)

Date: 2026-09-11
Status: Approved design, pending spec review

## Context

Phases 1-3a are merged. `testudo-watch serve` has a dashboard (`/`) showing the
last 50 notifications inline via `build_notifications_view`/`_notifications.html`,
and a `/watches` page for editing watches. The `notifications` table
(`id, course_id, term_id, section_id, open_seats, sent_at, channel, status,
detail`) already holds full history — nothing is ever deleted from it except
when a watch is hard-deleted via `/watches` (which also clears that
course/term's rows).

Phase 3 as roadmapped bundles notification history and new notifier channels.
This spec is **Phase 3b — notification history only**; new notifier channels
are a separate later spec.

## Goal

A `/notifications` page: the full history, filterable by course and date
range, paginated. Read-only, purely additive — no schema change, no new write
path.

Out of scope for 3b: filtering by status/channel (explicitly declined);
exporting history; editing or deleting individual notification rows from the
UI (deletion already happens as a side effect of deleting a watch); new
notifier channels.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Page scope | Separate `/notifications` page. The dashboard's existing last-50 fragment stays as a lightweight preview, now linking to the full page. |
| Filters | Course and date range only (status/channel filters declined as unnecessary). |
| Pagination | Page-number pagination via query-string params (`?course=&since=&until=&page=`), plain links, no HTMX — bookmarkable, consistent with the "no live refresh needed for a history browse" nature of the page. |

## Architecture

### Changed / new files

```
src/testudo_watch/db.py                       # NotificationPage dataclass; query_notifications(...); notification_course_ids()
src/testudo_watch/web.py                      # HistoryView dataclass; build_history_view(...); GET /notifications route
src/testudo_watch/templates/notifications.html # full history page: filter form + table + prev/next
src/testudo_watch/templates/_notifications.html # dashboard fragment: add a "View full history ->" link
src/testudo_watch/templates/dashboard.html    # nav: add "Notification history"
src/testudo_watch/templates/manage.html       # nav: add "Notification history"
tests/test_db.py                              # query_notifications filtering/pagination; notification_course_ids
tests/test_web_history.py                     # new: GET /notifications — filters, pagination, edge cases
README.md                                     # document /notifications
```

No file is deleted. `engine.py`, `scraper.py`, `config.py`, `notifier/`,
`diff.py`, `cli.py` are unchanged (no new CLI surface — `/notifications` is
reached through the existing `serve` command). `NotificationRow` and
`NotificationView` (Phase 2) are reused unchanged.

### `db.py`

```python
@dataclass(frozen=True)
class NotificationPage:
    rows: list[NotificationRow]
    total: int
```

`query_notifications(self, *, course_id: str | None = None, since: str | None
= None, until: str | None = None, page: int = 1, page_size: int = 50) ->
NotificationPage`:

- Builds a `WHERE` clause from whichever of `course_id` (`course_id = ?`),
  `since` (`sent_at >= ? ` with `f"{since} 00:00:00"`), `until` (`sent_at <=
  ?` with `f"{until} 23:59:59"`) are given; parameterized throughout.
- `total = SELECT COUNT(*) FROM notifications {where}` with the same params.
- `rows = SELECT ... FROM notifications {where} ORDER BY id DESC LIMIT ?
  OFFSET ?` with `page_size` and `(page - 1) * page_size` appended to the
  params. `page` is assumed already clamped to `>= 1` by the caller (the web
  route does the clamping; the DB method trusts its input, consistent with
  every other method in this class).
- Reuses the exact `SELECT` column list `recent_notifications` already uses,
  mapped to `NotificationRow` the same way.

`notification_course_ids(self) -> list[str]`: `SELECT DISTINCT course_id FROM
notifications ORDER BY course_id`. Includes courses with no currently-active
watch, so history for a removed watch stays browsable.

Neither method touches `SCHEMA_VERSION` or `_SCHEMA` — no migration.

### `web.py`

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
```

`build_history_view(db, *, course_id, since, until, page, page_size=50, now)
-> HistoryView`:
- Calls `db.query_notifications(course_id=course_id, since=since, until=until,
  page=page, page_size=page_size)`.
- Maps `.rows` through the same per-row construction `build_notifications_view`
  already uses (producing `NotificationView` with `sent_age` via
  `humanize_age`).
- `total_pages = max(1, ceil(page_result.total / page_size))`.
- `has_prev = page > 1`; `has_next = page < total_pages`.
- `courses = db.notification_course_ids()`.

`GET /notifications` route (added inside `create_app`, following the existing
`GET /watches` pattern — open via `_open_db`, guidance page on
`(sqlite3.DatabaseError, SchemaError)`):
- Query params: `course` (str, optional), `since` / `until` (`"YYYY-MM-DD"`,
  optional), `page` (int, optional, default `1`).
- `page` is clamped: any non-positive or non-integer value becomes `1` (parsed
  defensively — a malformed `?page=abc` must not 500, same principle as every
  other user-editable input in this app).
- `since`/`until` are passed through as-is if they match `^\d{4}-\d{2}-\d{2}$`,
  else treated as absent (ignored, not rejected — this is a GET whose only
  consequence of a bad value is "filter didn't apply", not a write to protect).
- Renders `notifications.html` with the `HistoryView`.

### Templates

- `notifications.html`: full page (matches `manage.html`'s shell — nav,
  `<style>`, no HTMX script needed since this page has no live-updating
  fragment). A `<form method="get">` with a `<select name="course">`
  (populated from `view.courses`, plus an "All courses" default `""` option
  meaning no filter) and two `<input type="date">` for `since`/`until`, a
  submit button, and a "Clear filters" link back to `/notifications` with no
  query string. The form's current `view.course_id`/`.since`/`.until` values
  pre-select/pre-fill these controls, so reloading or paging doesn't lose the
  active filters. Below it, a table of `view.rows` (same columns as the
  dashboard's `_notifications.html`: time, course/section, seats, channel,
  status badge). Below the table, "Page {page} of {total_pages}" plus Prev/Next
  `<a>` links that carry the current `course`/`since`/`until` query params
  forward and are omitted (not just disabled) when `has_prev`/`has_next` is
  false.
- `_notifications.html` (dashboard fragment): add one line below the existing
  table/empty-state: a link `<a href="/notifications">View full history →</a>`.
- `dashboard.html`, `manage.html`: nav line gains `<a href="/notifications">Notification history</a>`.

### Error handling

| Condition | Behavior |
|---|---|
| DB missing/corrupt/newer-schema | Guidance page, HTTP 200 (same `_open_db` pattern as every other route) |
| `?page=` non-integer, zero, or negative | Treated as `page=1` |
| `?page=` beyond `total_pages` | Valid request, empty (or partial) row set, `has_next=False`; not an error |
| `?since`/`?until` malformed or `since > until` | Both silently ignored if malformed; a valid-but-inverted range (since after until) is passed through as-is and simply yields zero rows — not specially detected, since an empty result is the correct and self-explanatory outcome |
| No `notifications` rows at all | Table renders "No notifications yet.", pagination controls omitted (`total_pages == 1`, no prev/next) |

## Testing

- `tests/test_db.py`:
  - `query_notifications` with no filters returns everything newest-first,
    `total` equals the full row count.
  - filtering by `course_id` excludes other courses' rows and reduces `total`
    accordingly.
  - filtering by `since`/`until` includes boundary timestamps (a row at
    exactly `since 00:00:00` or `until 23:59:59` is included) and excludes
    rows just outside the range.
  - pagination: seed >`page_size` rows, confirm `page=1` and `page=2` return
    disjoint, correctly-ordered slices and `total` is the same on both pages.
  - `notification_course_ids` returns distinct, sorted course ids, including
    one whose watch was later deleted.
- `tests/test_web_history.py` (`TestClient`):
  - `GET /notifications` with no params renders all seeded rows and the
    course dropdown options.
  - each filter individually and combined narrows the rendered rows.
  - `page=2` shows a different row set than `page=1` when there are enough
    rows; Prev link present on page 2, absent on page 1; Next link absent on
    the last page.
  - `?page=abc`, `?page=0`, `?page=-5` all render page 1 without error.
  - `?since=not-a-date` is ignored (behaves as if omitted).
  - an empty database (no notifications yet) renders the empty-state message
    and no pagination controls.
  - the dashboard fragment's "View full history" link and the nav links on
    all three pages point at `/notifications`.

## Definition of done (Phase 3b)

- `http://127.0.0.1:8477/notifications` lists all notification history,
  newest first, 50 per page.
- Filtering by course and/or date range narrows the list; clearing filters
  restores the full history.
- Paging through more than 50 matching rows works via Prev/Next, preserving
  the active filters in the URL.
- The dashboard's notifications preview links to the full history page; all
  three pages (`/`, `/watches`, `/notifications`) cross-link via nav.
- `pytest` passes, 0 warnings.
- `README.md` documents `/notifications`.
