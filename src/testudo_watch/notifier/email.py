from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from testudo_watch.config import ConfigError
from testudo_watch.notifier import NotifierError

_REQUIRED_ENV = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "TO_EMAIL", "FROM_EMAIL")
_DEFAULT_SMTP_PORT = 587


class EmailNotifier:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        to_email: str,
        from_email: str,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.to_email = to_email
        self.from_email = from_email

    @classmethod
    def from_env(cls) -> "EmailNotifier":
        missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if missing:
            raise ConfigError(
                f"notifier = \"email\" requires env vars: {', '.join(missing)}"
            )
        return cls(
            smtp_host=os.environ["SMTP_HOST"],
            smtp_port=int(os.getenv("SMTP_PORT", _DEFAULT_SMTP_PORT)),
            username=os.environ["SMTP_USERNAME"],
            password=os.environ["SMTP_PASSWORD"],
            to_email=os.environ["TO_EMAIL"],
            from_email=os.environ["FROM_EMAIL"],
        )

    def send(self, message: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = "testudo-watch alert"
        msg["To"] = self.to_email
        msg["From"] = self.from_email
        msg.set_content(message)
        try:
            client = smtplib.SMTP(self.smtp_host, self.smtp_port)
            try:
                client.starttls()
                client.login(self.username, self.password)
                client.send_message(msg)
            finally:
                client.quit()
        except Exception as exc:  # smtplib raises many exception types
            raise NotifierError(f"Email send failed: {exc}") from exc
