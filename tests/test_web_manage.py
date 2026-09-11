import sqlite3

from fastapi.testclient import TestClient

from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.models import Watch
from testudo_watch.web import create_app


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30, notifier="console",
        db_path=str(db_path), log_dir="logs",
    )


def client(tmp_path, file_watches=None):
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])
    db.add_or_replace_ui_watch("MATH240", "202601", ())
    db.add_or_replace_ui_watch("PHYS161", "202601", ("0201",))
    db.set_watch_active("PHYS161", "202601", False)
    db.close()
    return TestClient(create_app(cfg(tmp_path / "s.db"), file_watches or []))


def test_watches_page_lists_active_and_disabled(tmp_path):
    r = client(tmp_path).get("/watches")
    assert r.status_code == 200
    assert "CMSC351" in r.text and "MATH240" in r.text
    assert "PHYS161" in r.text          # disabled section
    assert 'name="course_id"' in r.text  # add form present
    assert 'name="term_id"' in r.text
    assert 'name="sections"' in r.text


def test_dashboard_links_to_manage(tmp_path):
    db = Database(tmp_path / "s.db")
    db.close()
    r = TestClient(create_app(cfg(tmp_path / "s.db"), [])).get("/")
    assert '/watches' in r.text
