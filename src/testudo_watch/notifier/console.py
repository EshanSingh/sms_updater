from __future__ import annotations

import logging

_log = logging.getLogger("testudo_watch.notifier")


class ConsoleNotifier:
    def send(self, message: str) -> None:
        _log.warning("NOTIFY: %s", message)
        print(message)
