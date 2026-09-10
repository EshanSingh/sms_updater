from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Watch:
    course_id: str
    term_id: str
    sections: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SectionSnapshot:
    course_id: str
    term_id: str
    section_id: str
    total_seats: int
    open_seats: int
    waitlist: int


@dataclass(frozen=True)
class OpeningEvent:
    snapshot: SectionSnapshot
