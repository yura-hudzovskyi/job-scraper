"""Djinni source adapter: httpx + BeautifulSoup against public search/vacancy pages."""

from datetime import UTC, datetime

import httpx

from app.domain.jobs.models import NormalizedJob, RawJob
from app.integrations.sources.base import JobFetchResult, JobSearchCriteria
from app.integrations.sources.djinni import mapper, parser

_LISTING_URL = "https://djinni.co/jobs/"
_USER_AGENT = "job-scraper/0.1 (personal job-search assistant; not for redistribution)"


class DjinniAdapter:
    source_name = "djinni"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0)

    async def fetch_jobs(
        self,
        search: JobSearchCriteria,
        cursor: str | None = None,
    ) -> JobFetchResult:
        page = int(cursor) if cursor else 1
        params: dict[str, str | int] = {}
        if search.keywords:
            params["primary_keyword"] = search.keywords[0]
        if page > 1:
            params["page"] = page

        response = await self._client.get(_LISTING_URL, params=params)
        response.raise_for_status()
        entries = parser.parse_search_results(response.text)

        raw_jobs = [
            RawJob(
                source=self.source_name,
                external_id=entry["external_id"],
                url=entry["url"],
                payload={"title": entry["title"]},
                fetched_at=datetime.now(UTC),
            )
            for entry in entries
        ]
        next_cursor = str(page + 1) if entries else None
        return JobFetchResult(raw_jobs=raw_jobs, next_cursor=next_cursor)

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
