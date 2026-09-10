from testudo_watch.diff import detect_openings
from testudo_watch.models import SectionSnapshot, Watch

W_ALL = Watch("CMSC351", "202601", ())
W_ONE = Watch("CMSC351", "202601", ("0101",))


def snap(section_id, open_seats):
    return SectionSnapshot("CMSC351", "202601", section_id, 100, open_seats, 0)


def test_zero_to_positive_emits_event():
    prev = {"0101": snap("0101", 0)}
    curr = [snap("0101", 3)]
    events = detect_openings(prev, curr, W_ALL)
    assert [e.snapshot.section_id for e in events] == ["0101"]
    assert events[0].snapshot.open_seats == 3


def test_no_prior_and_open_emits_event():
    events = detect_openings({}, [snap("0101", 1)], W_ALL)
    assert [e.snapshot.section_id for e in events] == ["0101"]


def test_no_prior_and_closed_is_silent():
    assert detect_openings({}, [snap("0101", 0)], W_ALL) == []


def test_stays_open_is_silent():
    prev = {"0101": snap("0101", 5)}
    assert detect_openings(prev, [snap("0101", 4)], W_ALL) == []


def test_positive_to_zero_is_silent_and_resets():
    prev = {"0101": snap("0101", 5)}
    assert detect_openings(prev, [snap("0101", 0)], W_ALL) == []
    # After reset, a later reopen fires again.
    prev = {"0101": snap("0101", 0)}
    assert len(detect_openings(prev, [snap("0101", 2)], W_ALL)) == 1


def test_section_filter_limits_to_watched_ids():
    curr = [snap("0101", 3), snap("0201", 9)]
    events = detect_openings({}, curr, W_ONE)
    assert [e.snapshot.section_id for e in events] == ["0101"]


def test_multiple_sections_emit_multiple_events():
    curr = [snap("0101", 3), snap("0201", 9)]
    events = detect_openings({}, curr, W_ALL)
    assert sorted(e.snapshot.section_id for e in events) == ["0101", "0201"]
