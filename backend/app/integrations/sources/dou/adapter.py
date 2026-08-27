"""DOU source adapter: RSS for discovery, detail-page HTML for full description.

See docs/source-adapters.md for why RSS discovery is preferred over listing scraping.
"""

from app.domain.jobs.models import NormalizedJob, RawJob
from app.integrations.sources.base import JobFetchResult, JobSearchCriteria
from app.integrations.sources.dou import mapper, parser


class DouAdapter:
    source_name = "dou"

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
