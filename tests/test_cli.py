import textwrap

from testudo_watch import cli
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot


def write_config(tmp_path, notifier="console"):
    p = tmp_path / "watches.toml"
    log_dir = (tmp_path / "logs").as_posix()
    p.write_text(
        textwrap.dedent(
            f"""
            poll_interval_seconds = 30
            notifier = "{notifier}"
            log_dir = "{log_dir}"
            [[watch]]
            course_id = "CMSC351"
            term_id = "202601"
            sections = ["0101"]
            """
        ),
        encoding="utf-8",
    )
    return p


def test_main_returns_1_on_config_error(tmp_path, capsys):
    missing = tmp_path / "nope.toml"
    rc = cli.main(["run", "--config", str(missing)])
    assert rc == 1
    assert "config" in capsys.readouterr().err.lower()


def test_main_returns_2_when_logging_cannot_be_configured(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)

    def boom(log_dir, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(cli, "configure_logging", boom)
    rc = cli.main(["check-once", "--config", str(config_path)])
    assert rc == 2
    assert "logging" in capsys.readouterr().err.lower()


def test_list_returns_2_when_logging_cannot_be_configured(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)

    def boom(log_dir, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(cli, "configure_logging", boom)
    rc = cli.main(["list", "--config", str(config_path)])
    assert rc == 2
    assert "logging" in capsys.readouterr().err.lower()


def test_serve_returns_2_when_logging_cannot_be_configured(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path)

    def boom(log_dir, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(cli, "configure_logging", boom)
    rc = cli.main(["serve", "--config", str(config_path)])
    assert rc == 2
    assert "logging" in capsys.readouterr().err.lower()


def test_check_once_runs_one_pass(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    db_path = tmp_path / "state.db"

    calls = {}

    def fake_fetch(session, course_id, term_id):
        calls["hit"] = (course_id, term_id)
        return [SectionSnapshot(course_id, term_id, "0101", 100, 5, 0)]

    # The CLI forwards its own fetch_sections reference into engine.run,
    # so patching it here is what takes effect.
    monkeypatch.setattr("testudo_watch.cli.fetch_sections", fake_fetch)

    rc = cli.main(
        ["check-once", "--config", str(config_path), "--db", str(db_path)]
    )
    assert rc == 0
    assert calls["hit"] == ("CMSC351", "202601")

    from testudo_watch.models import Watch

    db = Database(db_path)
    snaps = db.get_snapshots(Watch("CMSC351", "202601", ("0101",)))
    assert snaps["0101"].open_seats == 5
    db.close()


def test_list_prints_watches_and_last_seen(tmp_path, capsys):
    config_path = write_config(tmp_path)
    db_path = tmp_path / "state.db"
    db = Database(db_path)
    from testudo_watch.models import Watch

    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.upsert_snapshots([SectionSnapshot("CMSC351", "202601", "0101", 100, 2, 0)])
    db.close()

    rc = cli.main(["list", "--config", str(config_path), "--db", str(db_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CMSC351" in out and "0101" in out and "2" in out


def test_serve_parser_builds_without_verbose():
    from testudo_watch.cli import _build_parser

    args = _build_parser().parse_args(
        ["serve", "--host", "0.0.0.0", "--port", "9001"]
    )
    assert args.command == "serve"
    assert args.host == "0.0.0.0" and args.port == 9001
    assert not hasattr(args, "verbose")


def test_serve_invokes_uvicorn_with_parsed_host_and_port(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    seen = {}

    def fake_run(app, host, port):
        seen["app"] = app
        seen["host"] = host
        seen["port"] = port

    def fake_create_app(config, file_watches):
        seen["config"] = config
        seen["file_watches"] = file_watches
        return object()

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("testudo_watch.web.create_app", fake_create_app)

    rc = cli.main(["serve", "--config", str(config_path), "--port", "1234"])
    assert rc == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 1234
    assert seen["config"].poll_interval_seconds == 30
    assert [w.course_id for w in seen["file_watches"]] == ["CMSC351"]
