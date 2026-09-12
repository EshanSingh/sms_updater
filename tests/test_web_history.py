import sqlite3

from fastapi.testclient import TestClient

from testudo_watch import web
from testudo_watch.config import AppConfig
from testudo_watch.db import Database
from testudo_watch.web import create_app


def cfg(db_path):
    return AppConfig(
        poll_interval_seconds=30,
        notifier="console",
        db_path=str(db_path),
        log_dir="logs",
    )


def _seed_notifications(db, entries):
    for course_id, section_id, open_seats, sent_at in entries:
        db.connection.execute(
            "INSERT INTO notifications "
            "(course_id, term_id, section_id, open_seats, sent_at, channel, status, detail) "
            "VALUES (?, '202601', ?, ?, ?, 'console', 'sent', '')",
            (course_id, section_id, open_seats, sent_at),
        )
    db.connection.commit()


def client(tmp_path, entries):
    db = Database(tmp_path / "s.db")
    _seed_notifications(db, entries)
    db.close()
    return TestClient(create_app(cfg(tmp_path / "s.db"), []))


def test_notifications_page_no_filters_lists_everything(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 2, "2026-09-02 10:00:00"),
        ],
    )
    r = c.get("/notifications")
    assert r.status_code == 200
    assert "CMSC351" in r.text and "MATH240" in r.text
    assert 'name="course"' in r.text  # filter form present


def test_notifications_page_filters_by_course(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("MATH240", "0111", 2, "2026-09-02 10:00:00"),
        ],
    )
    r = c.get("/notifications?course=CMSC351")
    assert r.status_code == 200
    assert "0101" in r.text and "0111" not in r.text


def test_notifications_page_filters_by_date_range(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", "0101", 1, "2026-09-01 10:00:00"),
            ("CMSC351", "0201", 1, "2026-09-20 10:00:00"),
        ],
    )
    r = c.get("/notifications?since=2026-09-01&until=2026-09-10")
    assert "0101" in r.text and "0201" not in r.text


def test_notifications_page_paginates_with_working_links(tmp_path):
    c = client(
        tmp_path,
        [
            ("CMSC351", f"0{i:03d}", 1, f"2026-09-01 10:{i:02d}:00")
            for i in range(60)
        ],
    )
    page1 = c.get("/notifications")
    assert 'href="?course=&amp;since=&amp;until=&amp;page=2"' in page1.text
    assert "Prev" not in page1.text

    page2 = c.get("/notifications?page=2")
    assert "Prev" in page2.text


def test_pager_hrefs_urlencode_filter_values(tmp_path):
    # A course value containing "&" is an unrealistic edge case today (real
    # course ids are alnum), but proves the pager doesn't break if that ever
    # changes — the filter value must survive round-tripping through the URL.
    weird_course = "CMSC351&X"
    c = client(
        tmp_path,
        [
            (weird_course, f"0{i:03d}", 1, f"2026-09-01 10:{i:02d}:00")
            for i in range(60)
        ],
    )
    r = c.get("/notifications?course=CMSC351%26X")
    assert "CMSC351%26X" in r.text  # value is percent-encoded, not injected raw
    assert "&amp;since=" in r.text  # param separators are HTML-escaped
    assert "CMSC351&X&since=" not in r.text  # never a raw, un-encoded ampersand run


def test_notifications_page_malformed_page_param_defaults_to_one(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    for bad in ("abc", "0", "-5", "9223372036854775807"):
        r = c.get(f"/notifications?page={bad}")
        assert r.status_code == 200
        assert "CMSC351" in r.text


def test_notifications_page_malformed_date_is_ignored(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/notifications?since=not-a-date")
    assert r.status_code == 200
    assert "CMSC351" in r.text  # filter ignored, row still shown


def test_notifications_page_empty_db_shows_empty_state(tmp_path):
    c = client(tmp_path, [])
    r = c.get("/notifications")
    assert r.status_code == 200
    assert "No notifications yet." in r.text


def test_notifications_page_shows_filtered_empty_message_when_filters_match_nothing(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/notifications?course=MATH240")
    assert r.status_code == 200
    assert "No notifications match these filters." in r.text
    assert "No notifications yet." not in r.text


def test_notifications_page_shows_failure_detail(tmp_path):
    db = Database(tmp_path / "s.db")
    db.connection.execute(
        "INSERT INTO notifications "
        "(course_id, term_id, section_id, open_seats, sent_at, channel, status, detail) "
        "VALUES ('CMSC351', '202601', '0101', 1, '2026-09-01 10:00:00', 'console', 'failed', 'connection refused')"
    )
    db.connection.commit()
    db.close()
    c = TestClient(create_app(cfg(tmp_path / "s.db"), []))
    r = c.get("/notifications")
    assert r.status_code == 200
    assert "connection refused" in r.text


def test_dashboard_and_manage_and_history_pages_cross_link(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    for path in ("/", "/watches", "/notifications"):
        r = c.get(path)
        assert 'href="/notifications"' in r.text


def test_dashboard_fragment_links_to_full_history(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/fragments/notifications")
    assert 'href="/notifications"' in r.text


def test_notifications_page_returns_guidance_on_mid_query_error(tmp_path, monkeypatch):
    def boom(db, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(web, "build_history_view", boom)
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/notifications")
    assert r.status_code == 200
    assert "testudo-watch run" in r.text
