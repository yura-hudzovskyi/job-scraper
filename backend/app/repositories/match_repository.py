"""Persistence for JobMatch. Enforces unique(user_id, canonical_job_id)."""

from app.domain.matching.models import JobMatch


class MatchRepository:
    async def upsert(self, match: JobMatch) -> JobMatch:
        raise NotImplementedError

    async def list_for_user(self, user_id: str, min_score: float = 0.0) -> list[JobMatch]:
        raise NotImplementedError
