from datetime import datetime, timezone

from testudo_watch.web_time import humanize_age, parse_db_utc


def _utc(y, mo, d, h, mi, s):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def test_parse_db_utc_returns_tz_aware_utc():
    dt = parse_db_utc("2026-09-10 04:28:24")
    assert dt.tzinfo is timezone.utc
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
        2026,
        9,
        10,
        4,
        28,
        24,
    )


def test_humanize_age_across_unit_boundaries():
    now = _utc(2026, 9, 10, 12, 0, 0)
    assert humanize_age(_utc(2026, 9, 10, 11, 59, 52), now) == "8s ago"
    assert humanize_age(_utc(2026, 9, 10, 11, 56, 0), now) == "4m ago"
    assert humanize_age(_utc(2026, 9, 10, 9, 0, 0), now) == "3h ago"
    assert humanize_age(_utc(2026, 9, 7, 12, 0, 0), now) == "3d ago"


def test_humanize_age_clamps_future_to_zero():
    now = _utc(2026, 9, 10, 12, 0, 0)
    assert humanize_age(_utc(2026, 9, 10, 12, 0, 5), now) == "0s ago"
