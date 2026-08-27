"""Parser regression tests against real (trimmed) DOU fixtures — see
docs/source-adapters.md. If DOU changes its markup, these fail before production does.
"""

from datetime import UTC, datetime
from pathlib import Path

from app.domain.jobs.models import RawJob
from app.integrations.sources.dou import mapper, parser

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dou"


def test_parse_rss_feed_extracts_jobs() -> None:
    rss_xml = (FIXTURES / "listing.xml").read_text(encoding="utf-8")
    jobs = parser.parse_rss_feed(rss_xml)

    assert len(jobs) == 3
    first = jobs[0]
    assert first["external_id"] == "371200"
    assert first["url"].startswith("https://jobs.dou.ua/companies/sag7-ventures/vacancies/371200/")
    assert "AI Engineer" in first["title"]
    assert "SAG7 Ventures" in first["title"]
    assert first["description_html"]
    assert first["published_at"] is not None


def test_parse_job_detail_extracts_structured_fields() -> None:
    html = (FIXTURES / "vacancy.html").read_text(encoding="utf-8")
    detail = parser.parse_job_detail(html)

    assert detail["title"] == "Lead Python Developer"
    assert detail["company"] == "Motorsport Network"
    assert detail["location_text"] == "віддалено"
    assert detail["salary_text"] == "$2000–3000"
    assert detail["remote"] is True
    assert "Motorsport Stats platform" in detail["description_html"]


def test_to_normalized_job_maps_detail_page() -> None:
    html = (FIXTURES / "vacancy.html").read_text(encoding="utf-8")
    raw_job = RawJob(
        source="dou",
        external_id="371199",
        url="https://jobs.dou.ua/companies/motorsport-network/vacancies/371199/",
        payload={"html": html},
        fetched_at=datetime.now(UTC),
    )

    normalized = mapper.to_normalized_job(raw_job)

    assert normalized.source == "dou"
    assert normalized.external_id == "371199"
    assert normalized.title == "Lead Python Developer"
    assert normalized.company == "Motorsport Network"
    assert normalized.location.remote is True
    assert normalized.location.cities == []
    assert normalized.salary is not None
    assert (normalized.salary.min, normalized.salary.max, normalized.salary.currency) == (
        2000,
        3000,
        "USD",
    )
    assert normalized.seniority == "senior"
    assert "Motorsport Stats platform" in normalized.description
