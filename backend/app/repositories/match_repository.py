"""Persistence for JobMatch. Enforces unique(user_id, canonical_job_id) so re-scoring
a job for a user updates the existing row instead of duplicating."""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import JobMatchModel
from app.domain.matching.models import (
    JobMatch,
    LlmAssessment,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)


def _to_llm_assessment(payload: dict[str, Any] | None) -> LlmAssessment | None:
    if payload is None:
        return None
    # JSONB round-trips the nested `recommendation` enum back as a plain str on
    # read — same wrinkle the top-level `recommendation` column already has,
    # re-wrap explicitly rather than let callers get a bare str where the type
    # says Recommendation.
    return LlmAssessment(**{**payload, "recommendation": Recommendation(payload["recommendation"])})


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
        llm_assessment=_to_llm_assessment(model.llm_assessment),
        skills_source=model.skills_source,
        scored_by=model.scored_by,
        scored_at=model.scored_at,
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
            "llm_assessment": (
                {**asdict(match.llm_assessment), "recommendation": match.llm_assessment.recommendation.value}
                if match.llm_assessment
                else None
            ),
            "skills_source": match.skills_source,
            "scored_by": match.scored_by,
            # Explicit, not the column's server_default — server_default only fires
            # on INSERT, so without this a rescore's on_conflict_do_update left the
            # original scored_at untouched forever, making it useless as a
            # "did the rescore actually finish" signal for the frontend to poll on
            # (see JobDetails.tsx).
            "scored_at": datetime.now(UTC),
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

    async def list_skipped_canonical_job_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """Canonical job ids this user's matches recommend skipping — used by
        JobService.list_jobs to hide them from the default jobs-list view (see
        JobRepository.list_canonical_jobs's exclude_ids). Cheap at this app's
        scale: a few hundred rows per user, one indexed query, no pagination."""
        result = await self._session.execute(
            select(JobMatchModel.canonical_job_id).where(
                JobMatchModel.user_id == user_id,
                JobMatchModel.recommendation == Recommendation.SKIP.value,
            )
        )
        return set(result.scalars())

    async def list_for_canonical_jobs(
        self, user_id: uuid.UUID, canonical_job_ids: list[uuid.UUID]
    ) -> dict[str, JobMatch]:
        """Batch lookup for the jobs list page — one query for a whole page of jobs
        instead of the frontend firing a separate /match request per row."""
        if not canonical_job_ids:
            return {}
        result = await self._session.execute(
            select(JobMatchModel).where(
                JobMatchModel.user_id == user_id,
                JobMatchModel.canonical_job_id.in_(canonical_job_ids),
            )
        )
        return {str(model.canonical_job_id): _to_job_match(model) for model in result.scalars()}

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
