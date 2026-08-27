"""Use case: list/get canonical jobs.

Matches and user actions (save/apply/reject) need JobMatch (Phase 2 matching) and an
application tracker (Phase 5) respectively — see docs/roadmap.md.
"""

import uuid

from app.domain.jobs.models import CanonicalJob
from app.repositories.job_repository import JobRepository


class JobService:
    def __init__(self, job_repository: JobRepository):
        self._job_repository = job_repository

    async def list_jobs(self) -> list[CanonicalJob]:
        return await self._job_repository.list_canonical_jobs()

    async def get_job(self, canonical_job_id: uuid.UUID) -> CanonicalJob | None:
        return await self._job_repository.get_canonical_job(canonical_job_id)
