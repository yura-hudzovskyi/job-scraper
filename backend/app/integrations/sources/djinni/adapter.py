"""Djinni source adapter: httpx + BeautifulSoup against public search/vacancy pages."""

from app.domain.jobs.models import NormalizedJob, RawJob
from app.integrations.sources.base import JobFetchResult, JobSearchCriteria
from app.integrations.sources.djinni import mapper, parser


class DjinniAdapter:
    source_name = "djinni"

    async def fetch_jobs(
        self,
        search: JobSearchCriteria,
        cursor: str | None = None,
    ) -> JobFetchResult:
        raise NotImplementedError

    async def fetch_job_details(self, external_id: str, url: str) -> RawJob:
        raise NotImplementedError

    def normalize(self, raw_job: RawJob) -> NormalizedJob:
        return mapper.to_normalized_job(raw_job)
