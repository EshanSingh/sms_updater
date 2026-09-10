import pytest

from testudo_watch.config import AppConfig, ConfigError
from testudo_watch.notifier import NotifierError, build_notifier
from testudo_watch.notifier.console import ConsoleNotifier
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
