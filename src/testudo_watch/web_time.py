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
