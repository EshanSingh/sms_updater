from __future__ import annotations

from testudo_watch.models import OpeningEvent, SectionSnapshot, Watch


def detect_openings(
    prev: dict[str, SectionSnapshot],
    curr: list[SectionSnapshot],
    watch: Watch,
) -> list[OpeningEvent]:
    wanted = set(watch.sections)
    events: list[OpeningEvent] = []
    for snapshot in curr:
        if wanted and snapshot.section_id not in wanted:
            continue
        if snapshot.open_seats <= 0:
            continue
        previous = prev.get(snapshot.section_id)
        if previous is None or previous.open_seats == 0:
            events.append(OpeningEvent(snapshot=snapshot))
    return events
