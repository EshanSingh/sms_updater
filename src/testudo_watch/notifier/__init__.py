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
