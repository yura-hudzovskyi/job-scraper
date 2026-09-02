"""Maps DOU's parsed payload into a source-independent NormalizedJob."""

from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, RawJob
from app.integrations.sources.dou import parser
from app.integrations.sources.text_utils import guess_seniority, html_to_text, parse_salary_range

_REMOTE_MARKER = "віддалено"


def _split_location(location_text: str | None, remote: bool) -> JobLocation:
    if not location_text:
        return JobLocation(remote=remote, countries=[], cities=[])
    parts = [part.strip() for part in location_text.split(",") if part.strip()]
    cities = [part for part in parts if _REMOTE_MARKER not in part.lower()]
    return JobLocation(remote=remote, countries=[], cities=cities)


def to_normalized_job(raw_job: RawJob) -> NormalizedJob:
    """Requires raw_job.payload["html"] — the fetched vacancy detail page."""
    detail = parser.parse_job_detail(raw_job.payload["html"])
    title = detail["title"] or raw_job.payload.get("title", "")
    description_html = detail["description_html"] or raw_job.payload.get("description_html", "")

    return NormalizedJob(
        source="dou",
        external_id=raw_job.external_id,
        url=raw_job.url,
        title=title,
        company=detail["company"],
        description=html_to_text(description_html),
        employment_type=EmploymentType.FULL_TIME,
        location=_split_location(detail["location_text"], detail["remote"]),
        salary=parse_salary_range(detail["salary_text"]),
        seniority=guess_seniority(title),
        required_experience_years=None,
    )
