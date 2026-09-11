# testudo-watch

Watches the UMD Testudo Schedule of Classes for open seats in the course
sections you care about, and notifies you (console log, Twilio SMS, or email)
the moment a watched section goes from 0 open seats to 1 or more.

## Install

Requires Python 3.11+.

```bash
python -m pip install -e ".[dev]"      # or: pip install -r requirements.txt
```

## Configure

Edit `watches.toml`:

```toml
poll_interval_seconds = 30      # minimum 15
notifier = "console"            # "console", "sms", or "email"

[[watch]]
course_id = "CMSC351"
term_id = "202601"              # set to the current Testudo term
sections = ["0101", "0201"]     # empty = notify if ANY section opens
```

For SMS, copy `.env.example` to `.env` and fill in your Twilio
`ACCOUNT_SID`, `AUTH_TOKEN`, `TO_NUMBER`, and `FROM_NUMBER`, then set
`notifier = "sms"`.

For email, fill in `SMTP_HOST`, `SMTP_PORT` (defaults to 587/STARTTLS),
`SMTP_USERNAME`, `SMTP_PASSWORD`, `TO_EMAIL`, and `FROM_EMAIL` in `.env`,
then set `notifier = "email"`. Only one notifier is active at a time.

## Use

```bash
testudo-watch check-once   # one pass, prints parsed seat counts
testudo-watch run          # poll forever; Ctrl-C to stop
testudo-watch list         # show watches and last-seen seat counts
testudo-watch serve      # web dashboard at http://127.0.0.1:8477
```

State is kept in `testudo_watch.db` (SQLite); logs in `logs/testudo_watch.log`.
Stopping and restarting does not re-notify for a section that is still open.

## Web dashboard

With the watcher running (`testudo-watch run`), start the dashboard in
another terminal:

```bash
testudo-watch serve
```

Open <http://127.0.0.1:8477/>. It shows each configured watch with live
per-section seat counts, recent notifications, and when the watcher last polled
(with a warning banner if it looks stopped). The dashboard itself only reads
`testudo_watch.db`; watches are added, edited, and removed from the separate
`/watches` page (below). `serve` creates and migrates `testudo_watch.db` on
first use, just like `run` does, and enables SQLite's WAL journal mode so the
two processes' writes don't collide. Override the bind address with `--host` /
`--port`.

**Security note:** `serve` has no authentication and should only be run on a
trusted machine. It does reject cross-origin write requests (protecting
against a malicious page trying to edit your watches from another site), but
anyone who can reach the port can view and edit your watches.

The dashboard reflects what the watcher last wrote to the database — editing
`watches.toml` has no effect until `testudo-watch run` is restarted.

### Managing watches from the browser

`http://127.0.0.1:8477/watches` lists every watch and lets you add, edit the
section list, enable/disable, or delete one. Adds and edits are checked against
Testudo live — a bad course id or term is rejected on the spot. Changes take
effect on the watcher's next poll with no restart.

Watches you edit here are marked UI-owned and are **not** overwritten or removed
when `testudo-watch run` re-reads `watches.toml` on startup. Deleting a watch
that is still listed in `watches.toml` disables it instead (remove it from the
file to delete it for good).

### Browsing notification history

`http://127.0.0.1:8477/notifications` lists every notification ever sent,
filterable by course and date range, 50 per page. The dashboard's notification
panel only shows the most recent 50 — use this page to look further back or
narrow down to one course.

## Test

```bash
python -m pytest
```
