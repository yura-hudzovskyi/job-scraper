"""Orchestrates the Raw -> Normalized -> Canonical pipeline for one source: discover,
fetch details for genuinely new jobs only, normalize, dedup, persist.

Already-known jobs are skipped before any detail fetch — see docs/source-adapters.md
("detail HTML is only fetched for jobs not already seen"). Re-running this for a
source is always safe: raw_jobs and job_source_records are upserted on
(source, external_id), so nothing duplicates.

One bad listing (a detail page that fails to fetch, or normalizes into something
the adapter can't parse) must not sink the rest of the batch — see ingest_source,
which isolates each listing's failure the same way scrape.fetch_source already
isolates one source's failure from the rest of the platform.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.jobs.deduplication import DeduplicationService
from app.domain.jobs.models import NormalizedJob, RawJob
from app.integrations.sources.base import JobSearchCriteria, JobSourceAdapter
from app.repositories.job_repository import JobRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    jobs_seen: int
    jobs_processed: int
    processed_canonical_job_ids: list[str]


class JobIngestionService:
    def __init__(
        self,
        job_repository: JobRepository,
        dedup_service: DeduplicationService | None = None,
    ):
        self._job_repository = job_repository
        self._dedup_service = dedup_service or DeduplicationService()

    async def ingest_source(
        self,
        adapter: JobSourceAdapter,
        search: JobSearchCriteria,
        max_jobs: int | None = None,
    ) -> IngestionResult:
        """max_jobs caps how many discovered listings this call will even attempt to
        detail-fetch — a safety ceiling on run cost, not a guarantee of exactly N
        newly-processed jobs (already-known listings still get skipped for free
        within that cap, same as without one)."""
        discovery = await adapter.fetch_jobs(search)
        listings = discovery.raw_jobs[:max_jobs] if max_jobs is not None else discovery.raw_jobs

        canonical_job_ids: list[str] = []
        for listing in listings:
            if await self._job_repository.raw_job_exists(listing.source, listing.external_id):
                continue

            try:
                detail_raw_job = await adapter.fetch_job_details(listing.external_id, listing.url)
                canonical_job_id = await self._ingest_one(adapter, detail_raw_job)
            except Exception:
                logger.warning(
                    "failed to ingest listing %s/%s — skipping it, continuing with the rest "
                    "of this batch",
                    listing.source,
                    listing.external_id,
                    exc_info=True,
                )
                continue
            canonical_job_ids.append(str(canonical_job_id))

        return IngestionResult(
            jobs_seen=len(discovery.raw_jobs),
            jobs_processed=len(canonical_job_ids),
            processed_canonical_job_ids=canonical_job_ids,
        )

    async def ingest_raw_job(self, adapter: JobSourceAdapter, raw_job: RawJob) -> uuid.UUID:
        """Normalize + dedup a single already-fetched RawJob. Used by the `normalize`
        worker task when raw storage and detail-fetching already happened separately."""
        raw_job_id = await self._job_repository.upsert_raw_job(raw_job)
        return await self._ingest_one(adapter, raw_job, raw_job_id=raw_job_id)

    async def _ingest_one(
        self,
        adapter: JobSourceAdapter,
        raw_job: RawJob,
        raw_job_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if raw_job_id is None:
            raw_job_id = await self._job_repository.upsert_raw_job(raw_job)

        normalized: NormalizedJob = adapter.normalize(raw_job)
        canonical_job_id = await self._dedup(normalized)
        await self._job_repository.save_normalized_job(raw_job_id, normalized, canonical_job_id)
        return canonical_job_id

    async def _dedup(self, normalized: NormalizedJob) -> uuid.UUID:
        candidates = await self._job_repository.list_canonical_jobs()
        match = self._dedup_service.find_canonical_match(normalized, candidates)

        if match is not None:
            canonical_job_id = uuid.UUID(match.id)
            await self._job_repository.touch_canonical_job(canonical_job_id)
            return canonical_job_id

        return await self._job_repository.create_canonical_job(normalized)
