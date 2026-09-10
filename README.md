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
