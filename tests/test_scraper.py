import pytest
import requests

from testudo_watch.models import SectionSnapshot
from testudo_watch.scraper import (
    ScrapeError,
    build_session,
    fetch_sections,
    parse_sections,
)


@pytest.fixture
def sample_html(fixtures_dir):
    return (fixtures_dir / "sections_sample.html").read_text(encoding="utf-8")


def test_parse_returns_one_snapshot_per_section(sample_html):
    snaps = parse_sections(sample_html, "CMSC351", "202508")
    assert snaps == [
        SectionSnapshot("CMSC351", "202508", "0101", 200, 6, 0),
        SectionSnapshot("CMSC351", "202508", "0201", 90, 0, 12),
    ]


def test_parse_raises_when_no_sections():
    with pytest.raises(ScrapeError, match="no sections"):
        parse_sections("<div>nothing here</div>", "CMSC351", "202508")


def test_parse_raises_when_seat_count_missing():
    broken = """
    <div class="section">
      <input type="hidden" name="sectionId" value="0101" />
      <span class="seats-info"><span class="open-seats-count">x</span></span>
    </div>
    """
    with pytest.raises(ScrapeError):
        parse_sections(broken, "CMSC351", "202508")


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_fetch_sections_parses_ok(monkeypatch, sample_html):
    session = build_session()

    def fake_get(url, timeout):
        assert "202508/sections" in url
        assert timeout == 10
        return _FakeResponse(200, sample_html)

    monkeypatch.setattr(session, "get", fake_get)
    snaps = fetch_sections(session, "CMSC351", "202508")
    assert [s.section_id for s in snaps] == ["0101", "0201"]


def test_fetch_sections_raises_on_non_200(monkeypatch):
    session = build_session()
    monkeypatch.setattr(session, "get", lambda url, timeout: _FakeResponse(503, ""))
    with pytest.raises(ScrapeError, match="503"):
        fetch_sections(session, "CMSC351", "202508")


def test_fetch_sections_raises_on_request_exception(monkeypatch):
    session = build_session()

    def boom(url, timeout):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(session, "get", boom)
    with pytest.raises(ScrapeError):
        fetch_sections(session, "CMSC351", "202508")
