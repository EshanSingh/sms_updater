from __future__ import annotations

import argparse
import dataclasses
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
    for name in ("run", "check-once", "list", "serve"):
        p = sub.add_parser(name)
        p.add_argument("--config", default="watches.toml")
        p.add_argument("--db", default=None)
        if name not in ("list", "serve"):
            p.add_argument("--verbose", action="store_true")
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", default=8477, type=int)
    return parser


def _load(args):
    config, watches = load_config(args.config)
    if args.db:
        config = dataclasses.replace(config, db_path=args.db)
    return config, watches


def _cmd_list(config, watches) -> int:
    db = Database(config.db_path)
    try:
        for watch in watches:
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


def _cmd_serve(config, watches, *, host: str, port: int) -> int:
    import uvicorn

    from testudo_watch.web import create_app

    uvicorn.run(create_app(config, watches), host=host, port=port)
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
        try:
            return _cmd_list(config, watches)
        except Exception:  # noqa: BLE001
            _log.exception("fatal error")
            return 2

    if args.command == "serve":
        configure_logging(config.log_dir)
        try:
            return _cmd_serve(config, watches, host=args.host, port=args.port)
        except KeyboardInterrupt:
            return 0
        except Exception:  # noqa: BLE001
            _log.exception("fatal error")
            return 2

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
