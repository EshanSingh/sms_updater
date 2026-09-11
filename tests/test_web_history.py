from fastapi.testclient import TestClient

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
    assert 'href="?course=&since=&until=&page=2"' in page1.text
    assert "Prev" not in page1.text

    page2 = c.get("/notifications?page=2")
    assert "Prev" in page2.text
    assert "Next" not in page2.text  # 60 rows / 50 per page = exactly 2 pages


def test_notifications_page_malformed_page_param_defaults_to_one(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    for bad in ("abc", "0", "-5"):
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


def test_dashboard_and_manage_and_history_pages_cross_link(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    for path in ("/", "/watches", "/notifications"):
        r = c.get(path)
        assert 'href="/notifications"' in r.text


def test_dashboard_fragment_links_to_full_history(tmp_path):
    c = client(tmp_path, [("CMSC351", "0101", 1, "2026-09-01 10:00:00")])
    r = c.get("/fragments/notifications")
    assert 'href="/notifications"' in r.text
