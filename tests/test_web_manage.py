import sqlite3

from starlette.testclient import TestClient

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


def test_toggle_active_off_then_on(tmp_path, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path)
    r = c.post("/watches/MATH240/202601/active", data={"active": "0"})
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    assert db.watch_row("MATH240", "202601").active is False
    db.close()
    c.post("/watches/MATH240/202601/active", data={"active": "1"})
    db = Database(tmp_path / "s.db")
    row = db.watch_row("MATH240", "202601")
    assert row.active is True and row.source == "ui"
    db.close()


def test_toggle_404_when_absent(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/NOPE/202601/active", data={"active": "0"})
    assert r.status_code == 404


def test_delete_ui_only_watch_hard_deletes(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/MATH240/202601/delete")  # ui-added, not in file
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    assert db.watch_row("MATH240", "202601") is None
    db.close()


def test_delete_file_backed_watch_tombstones(tmp_path, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path, file_watches=[Watch("CMSC351", "202601", ("0101",))])
    r = c.post("/watches/CMSC351/202601/delete")
    assert r.status_code == 200
    assert "watches.toml" in r.text  # explains why it wasn't removed
    db = Database(tmp_path / "s.db")
    row = db.watch_row("CMSC351", "202601")
    assert row is not None and row.active is False
    db.close()


def test_delete_404_when_absent(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post("/watches/NOPE/202601/delete")
    assert r.status_code == 404


def test_get_routes_do_not_write(tmp_path, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path)
    db = Database(tmp_path / "s.db")
    before = {
        t: db.connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("watches", "section_snapshots", "notifications", "watch_health")
    }
    uv_before = db.connection.execute("PRAGMA user_version").fetchone()[0]
    db.close()
    reader = sqlite3.connect(str(tmp_path / "s.db"))
    data_version_before = reader.execute("PRAGMA data_version").fetchone()[0]
    for _ in range(3):
        c.get("/")
        c.get("/watches")
        c.get("/fragments/watches")
    data_version_after = reader.execute("PRAGMA data_version").fetchone()[0]
    reader.close()
    assert data_version_after == data_version_before
    db = Database(tmp_path / "s.db")
    after = {
        t: db.connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("watches", "section_snapshots", "notifications", "watch_health")
    }
    assert after == before
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == uv_before
    db.close()


def test_watches_page_shows_guidance_on_corrupt_db(tmp_path):
    p = tmp_path / "v99.db"
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    c = TestClient(create_app(cfg(str(p)), []))
    r = c.get("/watches")
    assert r.status_code == 200
    assert "testudo-watch run" in r.text


def test_add_watch_returns_503_on_corrupt_db(tmp_path, fake_probe):
    fake_probe(["0101"])
    p = tmp_path / "v99.db"
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    c = TestClient(create_app(cfg(str(p)), []))
    r = c.post("/watches", data={"course_id": "CMSC351", "term_id": "202601", "sections": ""})
    assert r.status_code == 503


def test_add_watch_rejects_cross_origin(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post(
        "/watches",
        data={"course_id": "CMSC330", "term_id": "202601", "sections": ""},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_add_watch_allows_no_origin_header(tmp_path, fake_probe):
    fake_probe(["0101"])
    r = client(tmp_path).post(
        "/watches", data={"course_id": "CMSC330", "term_id": "202601", "sections": ""}
    )
    assert r.status_code == 200  # unchanged: TestClient sends no Origin by default


def test_edit_sections_on_legacy_lowercase_row_updates_in_place(tmp_path, fake_probe):
    fake_probe(["0101", "0202"])
    db = Database(tmp_path / "s.db")
    db.connection.execute(
        "INSERT INTO watches (course_id, term_id, sections_csv, active, source) "
        "VALUES ('cmsc999', '202601', '0101', 1, 'file')"
    )
    db.connection.commit()
    db.close()
    c = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = c.post("/watches/cmsc999/202601/sections", data={"sections": "0101, 0202"})
    assert r.status_code == 200
    db = Database(tmp_path / "s.db")
    rows = db.connection.execute(
        "SELECT course_id, sections_csv FROM watches"
    ).fetchall()
    assert len(rows) == 1  # no duplicate row created
    assert rows[0]["sections_csv"] == "0101,0202"
    db.close()


def test_ui_watch_survives_a_sync(tmp_path, fake_probe):
    fake_probe(["0101", "0201"])
    c = client(tmp_path)
    c.post("/watches", data={"course_id": "CMSC330", "term_id": "202601",
                             "sections": "0101"})
    db = Database(tmp_path / "s.db")
    db.sync_watches([Watch("CMSC351", "202601", ("0101",))])  # run's startup sync
    row = db.watch_row("CMSC330", "202601")
    assert row is not None and row.active is True and row.source == "ui"
    db.close()


def test_watches_page_returns_guidance_on_mid_query_error(tmp_path, monkeypatch):
    def boom(db, file_watches):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(web, "build_manage_view", boom)
    r = client(tmp_path).get("/watches")
    assert r.status_code == 200
    assert "testudo-watch run" in r.text


def test_add_watch_returns_503_on_mid_query_write_error(tmp_path, monkeypatch, fake_probe):
    fake_probe(["0101"])
    c = client(tmp_path)

    def boom(self, course_id, term_id, sections):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Database, "add_or_replace_ui_watch", boom)
    r = c.post(
        "/watches", data={"course_id": "CMSC330", "term_id": "202601", "sections": ""}
    )
    assert r.status_code == 503


def test_slow_probe_does_not_block_other_concurrent_requests(tmp_path, monkeypatch):
    import threading
    import time

    def slow_probe(course_id, term_id):
        time.sleep(0.4)
        return ["0101"]

    monkeypatch.setattr(web, "_probe", slow_probe)
    results = {}

    # TestClient must be used as a context manager here: outside a `with`
    # block it spins up a brand-new event loop per request, so two threads
    # calling it would never actually share a loop to contend for and the
    # test wouldn't prove anything. Entered as a context manager, all calls
    # share one portal/event loop, matching how `serve` really runs.
    with client(tmp_path) as c:

        def do_slow_post():
            c.post(
                "/watches",
                data={"course_id": "CMSC330", "term_id": "202601", "sections": ""},
            )

        def do_fast_get():
            time.sleep(0.1)  # let the POST start and enter the slow probe first
            start = time.monotonic()
            c.get("/watches")
            results["get_duration"] = time.monotonic() - start

        t_post = threading.Thread(target=do_slow_post)
        t_get = threading.Thread(target=do_fast_get)
        t_post.start()
        t_get.start()
        t_post.join(timeout=5)
        t_get.join(timeout=5)

    # If the probe blocked the event loop, this GET would queue behind the
    # whole 0.4s sleep. It should complete almost immediately instead.
    assert results["get_duration"] < 0.3
