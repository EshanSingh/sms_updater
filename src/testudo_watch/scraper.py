from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from testudo_watch.models import SectionSnapshot

SECTIONS_URL = "https://app.testudo.umd.edu/soc/{term_id}/sections"
USER_AGENT = "testudo-watch/0.1 (UMD course seat notifier)"
REQUEST_TIMEOUT = 10


class ScrapeError(Exception):
    """Raised when Testudo cannot be fetched or its markup cannot be parsed."""


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _seat_count(section, class_name: str, *, default: int | None = None) -> int:
    node = section.find("span", class_=class_name)
    if node is None:
        if default is not None:
            return default
        raise ScrapeError(f"missing {class_name!r} in section markup")
    text = node.get_text(strip=True)
    try:
        return int(text)
    except ValueError as exc:
        raise ScrapeError(f"non-numeric {class_name!r}: {text!r}") from exc


def parse_sections(
    html: str, course_id: str, term_id: str
) -> list[SectionSnapshot]:
    soup = BeautifulSoup(html, "lxml")
    sections = soup.select("div.section")
    if not sections:
        raise ScrapeError(f"no sections found for {course_id} in term {term_id}")

    snapshots: list[SectionSnapshot] = []
    for section in sections:
        id_input = section.find("input", attrs={"name": "sectionId"})
        if id_input is not None and id_input.get("value"):
            section_id = id_input["value"].strip()
        else:
            id_span = section.find("span", class_="section-id")
            if id_span is None:
                raise ScrapeError("section block has no section id")
            section_id = id_span.get_text(strip=True)

        snapshots.append(
            SectionSnapshot(
                course_id=course_id,
                term_id=term_id,
                section_id=section_id,
                total_seats=_seat_count(section, "total-seats-count"),
                open_seats=_seat_count(section, "open-seats-count"),
                waitlist=_seat_count(section, "waitlist-count", default=0),
            )
        )
    return snapshots


def fetch_sections(
    session: requests.Session, course_id: str, term_id: str
) -> list[SectionSnapshot]:
    url = SECTIONS_URL.format(term_id=term_id) + f"?courseIds={course_id}"
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise ScrapeError(f"request to {url} failed: {exc}") from exc
    if response.status_code != 200:
        raise ScrapeError(f"Testudo returned HTTP {response.status_code} for {url}")
    return parse_sections(response.text, course_id, term_id)
