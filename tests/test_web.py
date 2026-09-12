import sqlite3
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot, Watch
from testudo_watch.web import build_manage_view, create_app


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path=str(db_path),
        log_dir="logs",
    )


def seed(tmp_path, *, heartbeat_at=None, cycle=42):
    hb = heartbeat_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.connection.execute(
        "INSERT INTO section_snapshots "
        "(course_id, term_id, section_id, total_seats, open_seats, waitlist, updated_at) "
        "VALUES ('CMSC351','202601','0101',200,4,0,?)",
        (hb,),
    )
    db.connection.execute(
        "INSERT INTO watcher_heartbeat (id, updated_at, cycle_count) VALUES (1, ?, ?)",
        (hb, cycle),
    )
    db.record_notification(
        SectionSnapshot("CMSC351", "202601", "0101", 4, 0, 0),
        channel="console",
        status="sent",
    )
    db.connection.commit()
    db.close()


def test_htmx_is_served_locally_not_from_a_cdn(tmp_path):
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "htmx" in r.text.lower()

    for path in ("/", "/watches"):
        page = client.get(path)
        assert "unpkg.com" not in page.text
        assert 'src="/static/htmx.min.js"' in page.text


def test_dashboard_renders_seeded_data(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "CMSC351" in body and "0101" in body and "cycle" in body


def test_fragment_routes_return_bare_partials(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    for path in ("/fragments/status", "/fragments/watches", "/fragments/notifications"):
        r = client.get(path)
        assert r.status_code == 200
        assert "<html" not in r.text.lower()


def test_status_fragment_does_not_build_unrelated_views(tmp_path, monkeypatch):
    from testudo_watch import web

    seed(tmp_path)

    def boom(*a, **k):
        raise AssertionError("should not be called for the status fragment")

    monkeypatch.setattr(web, "build_watches_view", boom)
    monkeypatch.setattr(web, "build_notifications_view", boom)
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/fragments/status")
    assert r.status_code == 200


def test_watches_fragment_does_not_build_unrelated_views(tmp_path, monkeypatch):
    from testudo_watch import web

    seed(tmp_path)

    def boom(*a, **k):
        raise AssertionError("should not be called for the watches fragment")

    monkeypatch.setattr(web, "build_status_view", boom)
    monkeypatch.setattr(web, "build_notifications_view", boom)
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/fragments/watches")
    assert r.status_code == 200


def test_notifications_fragment_does_not_build_unrelated_views(tmp_path, monkeypatch):
    from testudo_watch import web

    seed(tmp_path)

    def boom(*a, **k):
        raise AssertionError("should not be called for the notifications fragment")

    monkeypatch.setattr(web, "build_status_view", boom)
    monkeypatch.setattr(web, "build_watches_view", boom)
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/fragments/notifications")
    assert r.status_code == 200


def test_status_fragment_shows_stale_for_old_heartbeat(tmp_path):
    stale_at = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    seed(tmp_path, heartbeat_at=stale_at)
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/fragments/status")
    assert r.status_code == 200
    assert "stopped" in r.text.lower()


def test_status_fragment_healthy_for_fresh_heartbeat(tmp_path):
    seed(tmp_path)  # heartbeat ≈ now
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/fragments/status")
    assert "cycle" in r.text and "42" in r.text
    assert "stopped" not in r.text.lower()


def test_build_manage_view_splits_active_and_disabled(tmp_path):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])           # file, active
    db.add_or_replace_ui_watch("MATH240", "202601", ())                # ui, active
    db.add_or_replace_ui_watch("PHYS161", "202601", ("0201",))
    db.set_watch_active("PHYS161", "202601", False)                    # ui, disabled
    view = build_manage_view(db, [Watch("CMSC351", "202601", ("0101",))])
    assert [(r.course_id, r.source, r.in_file) for r in view.active] == [
        ("CMSC351", "file", True),
        ("MATH240", "ui", False),
    ]
    assert [(r.course_id, r.in_file) for r in view.disabled] == [("PHYS161", False)]
    assert view.active[0].section_label == "0101"
    assert view.active[1].section_label == ""
    db.close()


def test_missing_database_is_created_and_renders_empty_dashboard(tmp_path):
    # read-write serve creates + migrates the DB on first request, like `run`
    missing = tmp_path / "created.db"
    client = TestClient(create_app(cfg(missing), []))
    r = client.get("/")
    assert r.status_code == 200
    assert "testudo-watch run" in r.text  # "not completed a poll yet" guidance
    assert missing.exists()


def test_newer_schema_database_shows_guidance(tmp_path):
    p = tmp_path / "v99.db"
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    client = TestClient(create_app(cfg(str(p)), []))
    r = client.get("/")
    assert r.status_code == 200 and "testudo-watch" in r.text  # guidance page, not 500


def test_dashboard_returns_guidance_on_mid_query_error(tmp_path, monkeypatch):
    from testudo_watch import web

    def boom(db, config, *, now):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(web, "build_status_view", boom)
    db = Database(tmp_path / "s.db")
    db.close()
    client = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = client.get("/")
    assert r.status_code == 200
    assert "testudo-watch run" in r.text
