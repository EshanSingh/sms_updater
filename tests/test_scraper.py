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


def test_parse_missing_waitlist_span_defaults_to_zero():
    # Testudo omits the waitlist block entirely for sections with no waitlist.
    no_waitlist = """
    <div class="section">
      <input type="hidden" name="sectionId" value="0101" />
      <span class="seats-info">
        <span class="total-seats-count">30</span>
        <span class="open-seats-count">4</span>
      </span>
    </div>
    """
    snaps = parse_sections(no_waitlist, "CMSC351", "202601")
    assert snaps == [SectionSnapshot("CMSC351", "202601", "0101", 30, 4, 0)]


def test_parse_raises_on_non_numeric_seat_count():
    non_numeric = """
    <div class="section">
      <input type="hidden" name="sectionId" value="0101" />
      <span class="seats-info">
        <span class="total-seats-count">abc</span>
        <span class="open-seats-count">4</span>
      </span>
    </div>
    """
    with pytest.raises(ScrapeError, match="non-numeric"):
        parse_sections(non_numeric, "CMSC351", "202601")


def test_parse_still_raises_when_total_or_open_missing():
    missing_open = """
    <div class="section">
      <input type="hidden" name="sectionId" value="0101" />
      <span class="seats-info"><span class="total-seats-count">30</span></span>
    </div>
    """
    with pytest.raises(ScrapeError, match="open-seats-count"):
        parse_sections(missing_open, "CMSC351", "202601")


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_fetch_sections_parses_ok(monkeypatch, sample_html):
    session = build_session()

    def fake_get(url, params, timeout):
        assert "202508/sections" in url
        assert params == {"courseIds": "CMSC351"}
        assert timeout == 10
        return _FakeResponse(200, sample_html)

    monkeypatch.setattr(session, "get", fake_get)
    snaps = fetch_sections(session, "CMSC351", "202508")
    assert [s.section_id for s in snaps] == ["0101", "0201"]


def test_fetch_sections_encodes_course_id_as_query_param(monkeypatch, sample_html):
    session = build_session()
    captured = {}

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, sample_html)

    monkeypatch.setattr(session, "get", fake_get)
    fetch_sections(session, "CMSC 351&x", "202508")
    assert "CMSC 351&x" not in captured["url"]  # not hand-concatenated into the URL
    assert captured["params"] == {"courseIds": "CMSC 351&x"}  # requests encodes it


def test_fetch_sections_raises_on_non_200(monkeypatch):
    session = build_session()
    monkeypatch.setattr(session, "get", lambda url, params, timeout: _FakeResponse(503, ""))
    with pytest.raises(ScrapeError, match="503"):
        fetch_sections(session, "CMSC351", "202508")


def test_fetch_sections_raises_on_request_exception(monkeypatch):
    session = build_session()

    def boom(url, params, timeout):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(session, "get", boom)
    with pytest.raises(ScrapeError):
        fetch_sections(session, "CMSC351", "202508")
