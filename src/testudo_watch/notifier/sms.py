from __future__ import annotations

import os

from twilio.rest import Client

from testudo_watch.config import ConfigError
from testudo_watch.notifier import NotifierError

_REQUIRED_ENV = ("ACCOUNT_SID", "AUTH_TOKEN", "TO_NUMBER", "FROM_NUMBER")


class TwilioNotifier:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        to_number: str,
        from_number: str,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.to_number = to_number
        self.from_number = from_number

    @classmethod
    def from_env(cls) -> "TwilioNotifier":
        missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if missing:
            raise ConfigError(
                f"notifier = \"sms\" requires env vars: {', '.join(missing)}"
            )
        return cls(
            account_sid=os.environ["ACCOUNT_SID"],
            auth_token=os.environ["AUTH_TOKEN"],
            to_number=os.environ["TO_NUMBER"],
            from_number=os.environ["FROM_NUMBER"],
        )

    def send(self, message: str) -> None:
        try:
            client = Client(self.account_sid, self.auth_token)
            client.messages.create(
                to=self.to_number, from_=self.from_number, body=message
            )
        except Exception as exc:  # twilio raises many exception types
            raise NotifierError(f"Twilio send failed: {exc}") from exc
