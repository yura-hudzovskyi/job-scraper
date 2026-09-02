"""Persistence for JobMatch. unique(user_id, canonical_job_id) keeps re-matching
idempotent — a new run updates the existing row instead of duplicating it."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import JobMatchModel
from app.domain.matching.models import JobMatch, MatchDecision, Recommendation
from app.repositories.base import rows_affected


def _to_job_match(model: JobMatchModel) -> JobMatch:
    return JobMatch(
        id=str(model.id),
        user_id=str(model.user_id),
        canonical_job_id=str(model.canonical_job_id),
        eligible=model.eligible,
        filter_reasons=list(model.filter_reasons or []),
        score=model.score,
        similarity=model.similarity,
        relevance=model.relevance,
        rerank_position=model.rerank_position,
        recommendation=Recommendation(model.recommendation),
        embedding_model=model.embedding_model,
        rerank_model=model.rerank_model,
        rerank_weight=model.rerank_weight,
        scored_at=model.scored_at,
        decision=MatchDecision(model.decision),
    )


class MatchRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert_many(self, matches: list[JobMatch]) -> int:
        """One statement per match, one flush for the batch. A run writes a few
        hundred rows at most, so this stays comfortably inside a transaction."""
        if not matches:
            return 0
        for match in matches:
            common = {
                "eligible": match.eligible,
                "filter_reasons": list(match.filter_reasons),
                "score": match.score,
                "similarity": match.similarity,
                "relevance": match.relevance,
                "rerank_position": match.rerank_position,
                "recommendation": match.recommendation.value,
                "embedding_model": match.embedding_model,
                "rerank_model": match.rerank_model,
                "rerank_weight": match.rerank_weight,
                # Explicit rather than the column's server_default, which only
                # fires on INSERT: without this a re-match left scored_at frozen,
                # making it useless as a "did this finish" signal for the UI.
                "scored_at": datetime.now(UTC),
            }
            stmt = (
                insert(JobMatchModel)
                .values(
                    user_id=uuid.UUID(match.user_id),
                    canonical_job_id=uuid.UUID(match.canonical_job_id),
                    # Only ever set on first insert — deliberately absent from
                    # `common`, so a re-match never resets a decision the user
                    # already made from Telegram.
                    decision=match.decision.value,
                    **common,
                )
                .on_conflict_do_update(
                    index_elements=[JobMatchModel.user_id, JobMatchModel.canonical_job_id],
                    set_=common,
                )
            )
            await self._session.execute(stmt)
        await self._session.flush()
        return len(matches)

    async def list_skipped_canonical_job_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """Job ids hidden from the default jobs list — the pipeline scored them
        below the consider threshold, a hard filter rejected them, or the user
        rejected them from Telegram."""
        result = await self._session.execute(
            select(JobMatchModel.canonical_job_id).where(
                JobMatchModel.user_id == user_id,
                (JobMatchModel.recommendation == Recommendation.SKIP.value)
                | (JobMatchModel.eligible.is_(False))
                | (JobMatchModel.decision == MatchDecision.REJECTED.value),
            )
        )
        return set(result.scalars())

    async def set_decision(
        self, user_id: uuid.UUID, canonical_job_id: uuid.UUID, decision: MatchDecision
    ) -> JobMatch | None:
        result = await self._session.execute(
            select(JobMatchModel).where(
                JobMatchModel.user_id == user_id,
                JobMatchModel.canonical_job_id == canonical_job_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.decision = decision.value
        await self._session.flush()
        return _to_job_match(model)

    async def count_decisions(self, user_id: uuid.UUID) -> dict[MatchDecision, int]:
        """Pending/approved/rejected counts across eligible matches — shown as a
        progress footer on each Telegram card. Missing keys are zero."""
        result = await self._session.execute(
            select(JobMatchModel.decision, func.count())
            .where(JobMatchModel.user_id == user_id, JobMatchModel.eligible.is_(True))
            .group_by(JobMatchModel.decision)
        )
        return {MatchDecision(decision): count for decision, count in result.all()}

    async def count_by_recommendation(self, user_id: uuid.UUID) -> dict[str, int]:
        result = await self._session.execute(
            select(JobMatchModel.recommendation, func.count())
            .where(JobMatchModel.user_id == user_id)
            .group_by(JobMatchModel.recommendation)
        )
        return {recommendation: count for recommendation, count in result.all()}

    async def list_for_canonical_jobs(
        self, user_id: uuid.UUID, canonical_job_ids: list[uuid.UUID]
    ) -> dict[str, JobMatch]:
        """Batch lookup for the jobs list — one query for a whole page instead of
        one request per row."""
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
        job_matches, not canonical_jobs) before the matches themselves go."""
        if not canonical_job_ids:
            return []
        result = await self._session.execute(
            select(JobMatchModel.id).where(JobMatchModel.canonical_job_id.in_(canonical_job_ids))
        )
        return [row[0] for row in result.all()]

    async def delete_for_canonical_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        """Call only after notifications referencing these matches are gone."""
        if not canonical_job_ids:
            return
        await self._session.execute(
            delete(JobMatchModel).where(JobMatchModel.canonical_job_id.in_(canonical_job_ids))
        )
        await self._session.flush()

    async def count_all(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(JobMatchModel))
        return int(result.scalar_one())

    async def delete_all(self) -> int:
        result = await self._session.execute(delete(JobMatchModel))
        await self._session.flush()
        return rows_affected(result)
