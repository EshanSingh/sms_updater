import pytest

from testudo_watch.config import AppConfig, ConfigError
from testudo_watch.notifier import NotifierError, build_notifier
from testudo_watch.notifier.console import ConsoleNotifier
from testudo_watch.notifier.email import EmailNotifier
from testudo_watch.notifier.sms import TwilioNotifier


def cfg(notifier):
    return AppConfig(
        poll_interval_seconds=30,
        notifier=notifier,
        db_path="x.db",
        log_dir="logs",
    )


def test_console_notifier_prints_and_does_not_raise(capsys):
    ConsoleNotifier().send("CMSC351 0101 has 3 open seats")
    assert "CMSC351 0101 has 3 open seats" in capsys.readouterr().out


def test_build_notifier_console(capsys):
    assert isinstance(build_notifier(cfg("console")), ConsoleNotifier)


def test_build_notifier_sms_requires_env(monkeypatch):
    for key in ("ACCOUNT_SID", "AUTH_TOKEN", "TO_NUMBER", "FROM_NUMBER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigError):
        build_notifier(cfg("sms"))


def test_twilio_notifier_from_env_builds(monkeypatch):
    monkeypatch.setenv("ACCOUNT_SID", "AC123")
    monkeypatch.setenv("AUTH_TOKEN", "tok")
    monkeypatch.setenv("TO_NUMBER", "+15555550123")
    monkeypatch.setenv("FROM_NUMBER", "+15555550188")
    n = TwilioNotifier.from_env()
    assert n.to_number == "+15555550123"


def test_twilio_notifier_send_wraps_errors(monkeypatch):
    n = TwilioNotifier("AC123", "tok", "+15555550123", "+15555550188")

    class _BoomClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("twilio down")

    monkeypatch.setattr("testudo_watch.notifier.sms.Client", _BoomClient)
    with pytest.raises(NotifierError):
        n.send("hi")


def test_twilio_notifier_send_calls_client(monkeypatch):
    n = TwilioNotifier("AC123", "tok", "+15555550123", "+15555550188")
    captured = {}

    class _OkClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("testudo_watch.notifier.sms.Client", _OkClient)
    n.send("seat open")
    assert captured == {
        "to": "+15555550123",
        "from_": "+15555550188",
        "body": "seat open",
    }


def test_build_notifier_email_requires_env(monkeypatch):
    for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "TO_EMAIL", "FROM_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigError):
        build_notifier(cfg("email"))


def test_email_notifier_from_env_builds(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("TO_EMAIL", "me@example.com")
    monkeypatch.setenv("FROM_EMAIL", "alerts@example.com")
    monkeypatch.delenv("SMTP_PORT", raising=False)
    n = EmailNotifier.from_env()
    assert n.smtp_host == "smtp.example.com"
    assert n.smtp_port == 587
    assert n.to_email == "me@example.com"


def test_email_notifier_from_env_reads_custom_port(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USERNAME", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("TO_EMAIL", "me@example.com")
    monkeypatch.setenv("FROM_EMAIL", "alerts@example.com")
    n = EmailNotifier.from_env()
    assert n.smtp_port == 2525


def test_email_notifier_send_wraps_errors(monkeypatch):
    n = EmailNotifier(
        "smtp.example.com", 587, "alerts@example.com", "secret",
        "me@example.com", "alerts@example.com",
    )

    class _BoomSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            raise RuntimeError("smtp down")

        def quit(self):
            pass

    monkeypatch.setattr("testudo_watch.notifier.email.smtplib.SMTP", _BoomSMTP)
    with pytest.raises(NotifierError):
        n.send("hi")


def test_email_notifier_send_calls_smtp(monkeypatch):
    n = EmailNotifier(
        "smtp.example.com", 587, "alerts@example.com", "secret",
        "me@example.com", "alerts@example.com",
    )
    calls = []

    class _OkSMTP:
        def __init__(self, host, port):
            calls.append(("init", host, port))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, msg):
            calls.append(("send_message", msg["To"], msg["From"], msg.get_content().strip()))

        def quit(self):
            calls.append(("quit",))

    monkeypatch.setattr("testudo_watch.notifier.email.smtplib.SMTP", _OkSMTP)
    n.send("seat open")
    assert calls == [
        ("init", "smtp.example.com", 587),
        ("starttls",),
        ("login", "alerts@example.com", "secret"),
        ("send_message", "me@example.com", "alerts@example.com", "seat open"),
        ("quit",),
    ]
