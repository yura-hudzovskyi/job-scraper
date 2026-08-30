"""Use case: list/get canonical jobs.

Matches and user actions (save/apply/reject) need JobMatch (Phase 2 matching) and an
application tracker (Phase 5) respectively — see docs/roadmap.md.
"""

import uuid

from app.domain.jobs.models import CanonicalJob
from app.domain.matching.models import JobMatch
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository


class JobService:
    def __init__(self, job_repository: JobRepository, match_repository: MatchRepository):
        self._job_repository = job_repository
        self._match_repository = match_repository

    async def list_jobs(
        self, user_id: uuid.UUID, limit: int, offset: int, include_skipped: bool = False
    ) -> tuple[list[CanonicalJob], dict[str, JobMatch], int]:
        """One page of canonical jobs plus this user's match for each — a single pair
        of queries instead of the frontend fetching every job and then one match per
        job (see docs/api.md). By default excludes jobs this user's matches already
        recommend skipping (wrong profession, ineligible, etc.) — pass
        include_skipped=True to see everything regardless of recommendation."""
        exclude_ids: set[uuid.UUID] | None = None
        if not include_skipped:
            exclude_ids = await self._match_repository.list_skipped_canonical_job_ids(user_id)

        jobs = await self._job_repository.list_canonical_jobs(
            limit=limit, offset=offset, exclude_ids=exclude_ids
        )
        total = await self._job_repository.count_canonical_jobs(exclude_ids=exclude_ids)
        matches = await self._match_repository.list_for_canonical_jobs(
            user_id, [uuid.UUID(job.id) for job in jobs]
        )
        return jobs, matches, total

    async def get_job(self, canonical_job_id: uuid.UUID) -> CanonicalJob | None:
        return await self._job_repository.get_canonical_job(canonical_job_id)
