"""Maps Djinni's parsed payload into a source-independent NormalizedJob."""

from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, RawJob
from app.integrations.sources.djinni import parser
from app.integrations.sources.text_utils import guess_seniority, html_to_text, parse_salary_range


def to_normalized_job(raw_job: RawJob) -> NormalizedJob:
    """Requires raw_job.payload["html"] — the fetched vacancy page."""
    detail = parser.parse_vacancy_page(raw_job.payload["html"])
    title = detail["title"] or raw_job.payload.get("title", "")

    countries = [detail["countries_text"]] if detail["countries_text"] else []
    location = JobLocation(remote=detail["remote"], countries=countries, cities=[])

    return NormalizedJob(
        source="djinni",
        external_id=raw_job.external_id,
        url=raw_job.url,
        title=title,
        company=detail["company"],
        description=html_to_text(detail["description_html"]),
        employment_type=EmploymentType.FULL_TIME,
        location=location,
        salary=parse_salary_range(detail["salary_text"]),
        seniority=guess_seniority(title),
        required_experience_years=parser.parse_experience_years(detail["experience_text"]),
    )
