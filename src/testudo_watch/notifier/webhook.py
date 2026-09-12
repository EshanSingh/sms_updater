from __future__ import annotations

import os

import requests

from testudo_watch.config import ConfigError
from testudo_watch.notifier import NotifierError

_REQUIRED_ENV = ("WEBHOOK_URL",)
_TIMEOUT_SECONDS = 10


class WebhookNotifier:
    def __init__(self, url: str) -> None:
        self.url = url

    @classmethod
    def from_env(cls) -> "WebhookNotifier":
        missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if missing:
            raise ConfigError(
                f"notifier = \"webhook\" requires env vars: {', '.join(missing)}"
            )
        return cls(url=os.environ["WEBHOOK_URL"])

    def send(self, message: str) -> None:
        try:
            response = requests.post(
                self.url, json={"content": message}, timeout=_TIMEOUT_SECONDS
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise NotifierError(f"Webhook send failed: {exc}") from exc
