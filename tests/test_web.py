import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import SectionSnapshot, Watch
from testudo_watch.web import create_app


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


def test_dashboard_renders_seeded_data(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "CMSC351" in body and "0101" in body and "cycle" in body


def test_fragment_routes_return_bare_partials(tmp_path):
    seed(tmp_path)
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    for path in ("/fragments/status", "/fragments/watches", "/fragments/notifications"):
        r = client.get(path)
        assert r.status_code == 200
        assert "<html" not in r.text.lower()


def test_status_fragment_shows_stale_for_old_heartbeat(tmp_path):
    seed(tmp_path, heartbeat_at="2026-09-09 09:00:00")
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    r = client.get("/fragments/status")
    assert r.status_code == 200
    assert "stopped" in r.text.lower()


def test_status_fragment_healthy_for_fresh_heartbeat(tmp_path):
    seed(tmp_path)  # heartbeat ≈ now
    client = TestClient(create_app(cfg(tmp_path / "s.db")))
    r = client.get("/fragments/status")
    assert "cycle" in r.text and "42" in r.text
    assert "stopped" not in r.text.lower()


def test_missing_database_shows_guidance_not_500(tmp_path):
    client = TestClient(create_app(cfg(tmp_path / "missing.db")))
    r = client.get("/")
    assert r.status_code == 200 and "testudo-watch run" in r.text
    r2 = client.get("/fragments/watches")
    assert r2.status_code == 200 and "<html" not in r2.text.lower()


def test_pre_v2_database_shows_guidance(tmp_path):
    p = tmp_path / "v1.db"
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE watches (course_id TEXT, term_id TEXT, "
        "sections_csv TEXT, active INT, PRIMARY KEY(course_id, term_id))"
    )
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    client = TestClient(create_app(cfg(str(p))))
    r = client.get("/")
    assert r.status_code == 200 and "testudo-watch run" in r.text
