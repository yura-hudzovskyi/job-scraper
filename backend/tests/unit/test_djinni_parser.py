"""Parser regression tests against real (trimmed) Djinni fixtures — see
docs/source-adapters.md.
"""

from datetime import UTC, datetime
from pathlib import Path

from app.domain.jobs.models import RawJob
from app.integrations.sources.djinni import mapper, parser

FIXTURES = Path(__file__).parent.parent / "fixtures" / "djinni"


def test_parse_search_results_extracts_jobs() -> None:
    html = (FIXTURES / "listing.html").read_text(encoding="utf-8")
    jobs = parser.parse_search_results(html)

    assert len(jobs) == 2
    first = jobs[0]
    assert first["external_id"] == "845300"
    assert first["url"] == "https://djinni.co/jobs/845300-junior-motion-designer-ai-creator/"
    assert first["title"] == "Junior Motion Designer/AI Creator"
    assert jobs[1]["external_id"] == "845214"


def test_parse_vacancy_page_extracts_structured_fields() -> None:
    html = (FIXTURES / "vacancy.html").read_text(encoding="utf-8")
    detail = parser.parse_vacancy_page(html)

    assert detail["title"] == "Junior Motion Designer/AI Creator"
    assert detail["company"] == "Bitmedia Labs"
    assert detail["remote"] is True
    assert detail["salary_text"] == "$800-1000"
    assert detail["experience_text"] is not None
    assert "року" in detail["experience_text"]
    assert detail["countries_text"] == "Країни Європи та Україна"
    assert "Bitmedia Labs" in detail["description_html"]


def test_parse_experience_years() -> None:
    assert parser.parse_experience_years("Виключно від 1 року досвіду") == 1.0
    assert parser.parse_experience_years("Без досвіду") == 0.0
    assert parser.parse_experience_years(None) is None


def test_to_normalized_job_maps_detail_page() -> None:
    html = (FIXTURES / "vacancy.html").read_text(encoding="utf-8")
    raw_job = RawJob(
        source="djinni",
        external_id="845300",
        url="https://djinni.co/jobs/845300-junior-motion-designer-ai-creator/",
        payload={"html": html},
        fetched_at=datetime.now(UTC),
    )

    normalized = mapper.to_normalized_job(raw_job)

    assert normalized.source == "djinni"
    assert normalized.title == "Junior Motion Designer/AI Creator"
    assert normalized.company == "Bitmedia Labs"
    assert normalized.location.remote is True
    assert normalized.location.countries == ["Країни Європи та Україна"]
    assert normalized.salary is not None
    assert (normalized.salary.min, normalized.salary.max, normalized.salary.currency) == (
        800,
        1000,
        "USD",
    )
    assert normalized.seniority == "junior"
    assert normalized.required_experience_years == 1.0
    assert "Bitmedia Labs" in normalized.description
