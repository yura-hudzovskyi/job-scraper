"""Persistence for the evaluation set — spec 20.1.

Kept apart from the ranking code on purpose. This table is the yardstick, and a
repository that both wrote judgements and read scores would make it very easy to
one day measure the system against something the system produced.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.evaluation import EvaluationPairModel


@dataclass(frozen=True)
class PairToJudge:
    """One pair as an annotator needs to see it."""

    id: str
    canonical_job_id: str
    job_title: str
    job_company: str
    job_text: str
    system_score: float | None
    label: int | None
    tier: str


class EvaluationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add_pairs(self, rows: list[dict[str, Any]]) -> int:
        """Insert sampled pairs, skipping any already in the set.

        `on_conflict_do_nothing` rather than an upsert: a pair that is already
        there may already carry a judgement, and re-sampling must never reset
        one. Re-running the sampler after a new scrape is the normal way to grow
        the set, so this has to be safe rather than merely idempotent.
        """
        if not rows:
            return 0
        # RETURNING rather than rowcount: with ON CONFLICT DO NOTHING the driver
        # reports rows attempted on some paths and rows written on others, and
        # the number this returns is what a caller uses to decide the set grew.
        result = await self._session.execute(
            pg_insert(EvaluationPairModel)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_evaluation_pairs_candidate_job")
            .returning(EvaluationPairModel.id)
        )
        inserted = len(result.scalars().all())
        await self._session.flush()
        return inserted

    async def judge(self, pair_id: uuid.UUID, label: int, annotator: str) -> bool:
        """Record one judgement. False when there is no such pair.

        The timestamp is written with the label because the table's check
        constraint requires them to agree — a row that says it was judged but
        not when, or when but not what, cannot answer either question.
        """
        pair = await self._session.get(EvaluationPairModel, pair_id)
        if pair is None:
            return False
        pair.label = label
        pair.annotator = annotator
        pair.annotated_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def clear_judgement(self, pair_id: uuid.UUID) -> bool:
        """Undo a judgement, returning the pair to the queue.

        Both columns clear together, for the same reason they are written
        together. A mis-clicked label that cannot be taken back is a label
        somebody will work around by leaving the pair judged and wrong.
        """
        pair = await self._session.get(EvaluationPairModel, pair_id)
        if pair is None:
            return False
        pair.label = None
        pair.annotator = None
        pair.annotated_at = None
        await self._session.flush()
        return True

    async def existing_job_ids(self, candidate_revision_id: uuid.UUID) -> set[uuid.UUID]:
        """Canonical jobs already in this candidate's set, so sampling adds only new ones."""
        result = await self._session.execute(
            select(EvaluationPairModel.canonical_job_id).where(
                EvaluationPairModel.candidate_revision_id == candidate_revision_id
            )
        )
        return set(result.scalars())

    async def progress(self) -> dict[str, int]:
        """How much of the set is judged, per tier — what the System page shows.

        20.1's tiers are sizes with gates attached, so "how far along" is a real
        operational question rather than a curiosity: the seed tier gates Phase 6
        recall measurement, and the core tier gates threshold tuning.
        """
        result = await self._session.execute(
            select(
                EvaluationPairModel.tier,
                func.count().label("total"),
                func.count(EvaluationPairModel.label).label("judged"),
            ).group_by(EvaluationPairModel.tier)
        )
        counts: dict[str, int] = {}
        for tier, total, judged in result.all():
            counts[f"{tier}_total"] = int(total)
            counts[f"{tier}_judged"] = int(judged)
        return counts

    async def label_distribution(self) -> dict[int, int]:
        """How many pairs got each label.

        Worth looking at before trusting any metric: a set where everything is
        `0` measures nothing, and one where everything is `3` measures nothing
        either. Both happen when an annotator settles into a rhythm.
        """
        result = await self._session.execute(
            select(EvaluationPairModel.label, func.count())
            .where(EvaluationPairModel.label.is_not(None))
            .group_by(EvaluationPairModel.label)
        )
        return {int(label): int(count) for label, count in result.all()}
