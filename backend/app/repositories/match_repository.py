"""Persistence for JobMatch. Enforces unique(user_id, canonical_job_id) so re-scoring
a job for a user updates the existing row instead of duplicating."""

import uuid
from dataclasses import asdict

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import JobMatchModel
from app.domain.matching.models import (
    JobMatch,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)


def _to_job_match(model: JobMatchModel) -> JobMatch:
    return JobMatch(
        id=str(model.id),
        user_id=str(model.user_id),
        canonical_job_id=str(model.canonical_job_id),
        eligible=model.eligible,
        requirement_match=model.requirement_match,
        practical_fit=model.practical_fit,
        breakdown=ScoreBreakdown(**model.breakdown),
        strengths=[MatchReason(**reason) for reason in model.strengths],
        gaps=[MatchGap(**gap) for gap in model.gaps],
        recommendation=Recommendation(model.recommendation) if model.recommendation else None,
        skills_source=model.skills_source,
    )


class MatchRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, match: JobMatch) -> JobMatch:
        common = {
            "eligible": match.eligible,
            "requirement_match": match.requirement_match,
            "practical_fit": match.practical_fit,
            "breakdown": asdict(match.breakdown),
            "strengths": [asdict(reason) for reason in match.strengths],
            "gaps": [asdict(gap) for gap in match.gaps],
            "recommendation": match.recommendation.value if match.recommendation else None,
            "skills_source": match.skills_source,
        }
        stmt = (
            insert(JobMatchModel)
            .values(
                user_id=uuid.UUID(match.user_id),
                canonical_job_id=uuid.UUID(match.canonical_job_id),
                **common,
            )
            .on_conflict_do_update(
                index_elements=[JobMatchModel.user_id, JobMatchModel.canonical_job_id],
                set_=common,
            )
            .returning(JobMatchModel.id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        model = await self._session.get(JobMatchModel, result.scalar_one())
        assert model is not None
        return _to_job_match(model)

    async def list_for_user(self, user_id: uuid.UUID, min_score: float = 0.0) -> list[JobMatch]:
        result = await self._session.execute(
            select(JobMatchModel)
            .where(JobMatchModel.user_id == user_id, JobMatchModel.practical_fit >= min_score)
            .order_by(JobMatchModel.practical_fit.desc())
        )
        return [_to_job_match(model) for model in result.scalars()]

    async def get_for_canonical_job(
        self, user_id: uuid.UUID, canonical_job_id: uuid.UUID
    ) -> JobMatch | None:
        result = await self._session.execute(
            select(JobMatchModel).where(
                JobMatchModel.user_id == user_id,
                JobMatchModel.canonical_job_id == canonical_job_id,
            )
        )
        model = result.scalar_one_or_none()
        return _to_job_match(model) if model else None

    async def find_ids_for_canonical_jobs(
        self, canonical_job_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Read-only — lets the caller clean up notifications (which reference
        job_matches, not canonical_jobs directly) before delete_for_canonical_jobs
        actually removes the matches. See JobRetentionService for the ordering."""
        if not canonical_job_ids:
            return []
        result = await self._session.execute(
            select(JobMatchModel.id).where(JobMatchModel.canonical_job_id.in_(canonical_job_ids))
        )
        return [row[0] for row in result.all()]

    async def delete_for_canonical_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        """Call only after notifications referencing these matches are already
        deleted (see find_ids_for_canonical_jobs + NotificationRepository)."""
        if not canonical_job_ids:
            return
        await self._session.execute(
            delete(JobMatchModel).where(JobMatchModel.canonical_job_id.in_(canonical_job_ids))
        )
        await self._session.flush()
