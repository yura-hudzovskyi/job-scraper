import uuid
from datetime import UTC, datetime

import pytest

from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    RawJob,
)
from app.integrations.sources.base import JobFetchResult, JobSearchCriteria
from app.services.job_ingestion_service import JobIngestionService


def _raw_job(external_id: str) -> RawJob:
    return RawJob(
        source="dou",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        payload={},
        fetched_at=datetime.now(UTC),
    )


class _FakeAdapter:
    source_name = "dou"

    def __init__(self, listing_count: int):
        self._listings = [_raw_job(str(i)) for i in range(listing_count)]
        self.detail_fetch_count = 0

    async def fetch_jobs(self, search: JobSearchCriteria, cursor: str | None = None) -> JobFetchResult:
        return JobFetchResult(raw_jobs=self._listings, next_cursor=None)

    async def fetch_job_details(self, external_id: str, url: str) -> RawJob:
        self.detail_fetch_count += 1
        return _raw_job(external_id)

    def normalize(self, raw_job: RawJob) -> NormalizedJob:
        return NormalizedJob(
            source="dou",
            external_id=raw_job.external_id,
            url=raw_job.url,
            title="Some Job",
            company="Acme",
            description="...",
            employment_type=EmploymentType.FULL_TIME,
            location=JobLocation(remote=True),
            salary=None,
            seniority=None,
            required_experience_years=None,
        )


class _FakeJobRepository:
    def __init__(self) -> None:
        self._known: set[str] = set()

    async def raw_job_exists(self, source: str, external_id: str) -> bool:
        return external_id in self._known

    async def upsert_raw_job(self, raw_job: RawJob) -> uuid.UUID:
        self._known.add(raw_job.external_id)
        return uuid.uuid4()

    async def list_canonical_jobs(self) -> list:
        return []

    async def touch_canonical_job(self, canonical_job_id: uuid.UUID) -> None:
        pass

    async def create_canonical_job(self, normalized: NormalizedJob) -> uuid.UUID:
        return uuid.uuid4()

    async def save_normalized_job(self, raw_job_id, normalized, canonical_job_id) -> uuid.UUID:
        return uuid.uuid4()


@pytest.mark.asyncio
async def test_max_jobs_caps_how_many_listings_get_detail_fetched() -> None:
    adapter = _FakeAdapter(listing_count=10)
    service = JobIngestionService(_FakeJobRepository())  # type: ignore[arg-type]

    result = await service.ingest_source(adapter, JobSearchCriteria(keywords=["Python"]), max_jobs=3)

    assert adapter.detail_fetch_count == 3
    assert result.jobs_processed == 3
    assert result.jobs_seen == 10  # still reports the full discovery count


@pytest.mark.asyncio
async def test_no_max_jobs_processes_everything_discovered() -> None:
    adapter = _FakeAdapter(listing_count=5)
    service = JobIngestionService(_FakeJobRepository())  # type: ignore[arg-type]

    result = await service.ingest_source(adapter, JobSearchCriteria(keywords=["Python"]))

    assert adapter.detail_fetch_count == 5
    assert result.jobs_processed == 5
    assert result.jobs_seen == 5


class _OneBadListingAdapter(_FakeAdapter):
    """normalize() throws for exactly one listing — a stand-in for a real-world
    parsing edge case (missing field, unexpected HTML shape) in one posting."""

    def __init__(self, listing_count: int, bad_external_id: str):
        super().__init__(listing_count)
        self._bad_external_id = bad_external_id

    def normalize(self, raw_job: RawJob) -> NormalizedJob:
        if raw_job.external_id == self._bad_external_id:
            raise ValueError("could not parse this listing")
        return super().normalize(raw_job)


@pytest.mark.asyncio
async def test_one_bad_listing_does_not_stop_the_rest_of_the_batch() -> None:
    # Regression test: ingest_source used to let one listing's exception (a
    # detail-fetch or normalize failure) propagate out uncaught — since every
    # listing in a batch shares one session/transaction (see scrape.fetch_source),
    # that discarded every other listing already processed in the same batch too,
    # not just the bad one.
    adapter = _OneBadListingAdapter(listing_count=5, bad_external_id="2")
    service = JobIngestionService(_FakeJobRepository())  # type: ignore[arg-type]

    result = await service.ingest_source(adapter, JobSearchCriteria(keywords=["Python"]))

    assert result.jobs_seen == 5
    assert result.jobs_processed == 4
