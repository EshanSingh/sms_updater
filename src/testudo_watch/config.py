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
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
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
                course_id=str(entry["course_id"]).strip().upper(),
                term_id=str(entry["term_id"]),
                sections=tuple(sections),
            )
        )
    return config, watches
