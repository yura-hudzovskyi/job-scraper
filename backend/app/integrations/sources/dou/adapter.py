"""DOU source adapter: RSS for discovery, detail-page HTML for full description.

See docs/source-adapters.md for why RSS discovery is preferred over listing scraping.
"""

from datetime import UTC, datetime

import httpx

from app.domain.jobs.models import NormalizedJob, RawJob
from app.integrations.sources.base import JobFetchResult, JobSearchCriteria
from app.integrations.sources.dou import mapper, parser

_FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"
_USER_AGENT = "job-scraper/0.1 (personal job-search assistant; not for redistribution)"


class DouAdapter:
    source_name = "dou"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0)

    async def fetch_jobs(
        self,
        search: JobSearchCriteria,
        cursor: str | None = None,
    ) -> JobFetchResult:
        """DOU's feed isn't paginated — it returns the current top-N vacancies for the
        category, so cursor/next_cursor are unused here."""
        params = {"category": search.keywords[0]} if search.keywords else {}
        response = await self._client.get(_FEED_URL, params=params)
        response.raise_for_status()
        entries = parser.parse_rss_feed(response.text)

        raw_jobs = [
            RawJob(
                source=self.source_name,
                external_id=entry["external_id"],
                url=entry["url"],
                payload={"title": entry["title"], "description_html": entry["description_html"]},
                fetched_at=datetime.now(UTC),
            )
            for entry in entries
        ]
        return JobFetchResult(raw_jobs=raw_jobs, next_cursor=None)

    async def fetch_job_details(self, external_id: str, url: str) -> RawJob:
        response = await self._client.get(url)
        response.raise_for_status()
        return RawJob(
            source=self.source_name,
            external_id=external_id,
            url=url,
            payload={"html": response.text},
            fetched_at=datetime.now(UTC),
        )

    def normalize(self, raw_job: RawJob) -> NormalizedJob:
        return mapper.to_normalized_job(raw_job)
