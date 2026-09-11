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
    assert '<a href="/watches">' in r.text


import pytest

from testudo_watch import web


@pytest.fixture
def fake_probe(monkeypatch):
    calls = {}

    def _set(section_ids, *, raises=None):
        def probe(course_id, term_id):
            calls["args"] = (course_id, term_id)
            if raises:
                raise web.ProbeError(raises)
            return list(section_ids)

        monkeypatch.setattr(web, "_probe", probe)

    _set.calls = calls
    return _set


def test_add_watch_happy_path(tmp_path, fake_probe):
    fake_probe(["0101", "0201", "0301"])
    c = client(tmp_path)
    r = c.post("/watches", data={"course_id": "cmsc330", "term_id": "202601",
                                 "sections": "0101, 0201"})
    assert r.status_code == 200
    assert "CMSC330" in r.text
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CMSC330", "202601").source == "ui"
    db.close()


def test_add_watch_rejects_malformed_course(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches", data={"course_id": "cs", "term_id": "202601",
                                                "sections": ""})
    assert r.status_code == 422
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CS", "202601") is None
    db.close()


def test_add_watch_rejects_when_probe_fails(tmp_path, fake_probe):
    fake_probe([], raises="Testudo returned no sections for CMSC999 in 202601")
    r = client(tmp_path).post("/watches", data={"course_id": "CMSC999",
                                                "term_id": "202601", "sections": ""})
    assert r.status_code == 422
    assert "no sections" in r.text
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CMSC999", "202601") is None
    db.close()


def test_add_watch_rejects_unknown_section(tmp_path, fake_probe):
    fake_probe(["0101", "0201"])
    r = client(tmp_path).post("/watches", data={"course_id": "CMSC330",
                                                "term_id": "202601",
                                                "sections": "0101, 9999"})
    assert r.status_code == 422
    assert "9999" in r.text
    db = Database(tmp_path / "s.db")
    assert db.watch_row("CMSC330", "202601") is None
    db.close()


def test_edit_sections_happy_path(tmp_path, fake_probe):
    fake_probe(["0101", "0202"])
    r = client(tmp_path).post("/watches/CMSC351/202601/sections",
                              data={"sections": "0101, 0202"})
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    row = db.watch_row("CMSC351", "202601")
    assert row.sections == ("0101", "0202") and row.source == "ui"
    db.close()


def test_edit_sections_404_when_absent(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/NOPE/202601/sections",
                              data={"sections": "0101"})
    assert r.status_code == 404
