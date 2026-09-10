import dataclasses

import pytest

from testudo_watch.models import OpeningEvent, SectionSnapshot, Watch


def test_watch_defaults_sections_to_empty_tuple():
    w = Watch(course_id="CMSC351", term_id="202601")
    assert w.sections == ()


def test_watch_is_frozen():
    w = Watch(course_id="CMSC351", term_id="202601")
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.course_id = "CMSC330"


def test_section_snapshot_holds_seat_counts():
    s = SectionSnapshot(
        course_id="CMSC351",
        term_id="202601",
        section_id="0101",
        total_seats=200,
        open_seats=6,
        waitlist=0,
    )
    assert (s.total_seats, s.open_seats, s.waitlist) == (200, 6, 0)


def test_opening_event_wraps_snapshot():
    s = SectionSnapshot("CMSC351", "202601", "0101", 200, 6, 0)
    assert OpeningEvent(snapshot=s).snapshot is s
