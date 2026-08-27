"""Use case: list/filter jobs and matches for a user, and record user actions
(save/apply/reject) against a CanonicalJob."""

from app.domain.matching.models import JobMatch
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository


class JobService:
    def __init__(self, job_repository: JobRepository, match_repository: MatchRepository):
        self._job_repository = job_repository
        self._match_repository = match_repository

    async def list_matches(self, user_id: str, min_score: float = 0.0) -> list[JobMatch]:
        return await self._match_repository.list_for_user(user_id, min_score)

    async def record_action(self, user_id: str, canonical_job_id: str, action: str) -> None:
        raise NotImplementedError
