"""Persistence for RawJob / CanonicalJob, and dedup lookups.

Enforces unique(source, external_job_id) at the storage layer so repeated scrapes are
idempotent. See docs/domain-model.md.
"""

from app.domain.jobs.models import CanonicalJob, NormalizedJob, RawJob


class JobRepository:
    async def save_raw_job(self, raw_job: RawJob) -> None:
        raise NotImplementedError

    async def find_canonical_by_similarity(self, job: NormalizedJob) -> CanonicalJob | None:
        raise NotImplementedError

    async def upsert_canonical(self, job: CanonicalJob) -> CanonicalJob:
        raise NotImplementedError
