"""Building and scoring the evaluation set against real matches — spec 20.

Two jobs, and they are deliberately the only two. Sampling turns what the ranker
has already produced into a queue of pairs worth judging; scoring turns
judgements back into the numbers of 20.4. Nothing here decides whether a result
is good — that is 20.6's release gate, and it needs a comparison, not a run.

The single-candidate reality is worth stating rather than working around. 20.1
asks for 50-100 candidate profiles and a split by candidate to prevent leakage;
this is a personal job-search engine with one user and two CVs, so that split
does not exist and cannot be faked. What remains is still the question the
product actually asks — given this candidate, are the good vacancies at the top
— and it is exactly the question 3.5.2 condition 3 gates extraction on.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.evaluation.metrics import Judged, RankingMetrics, evaluate
from app.domain.evaluation.sampling import Candidate, coverage, stratified_sample
from app.repositories.evaluation_repository import EvaluationRepository

logger = logging.getLogger(__name__)

# Everything the ranker scored for this candidate, with the language of the text
# a person would read and the revision that text lives in. Ordered by score so
# the sampler's per-stratum ordering is stable even before it sorts.
_SCORED_PAIRS = """
    SELECT m.canonical_job_id::text,
           d.id::text AS job_revision_id,
           m.score,
           d.language_code
    FROM job_matches m
    JOIN canonical_jobs c ON c.id = m.canonical_job_id
    LEFT JOIN LATERAL (
        SELECT r.id, r.language_code
        FROM job_source_records s
        JOIN document_revisions r ON r.job_source_record_id = s.id
        WHERE s.canonical_job_id = m.canonical_job_id AND r.parsed_text IS NOT NULL
        ORDER BY r.created_at DESC
        LIMIT 1
    ) d ON TRUE
    WHERE m.user_id = :user_id
    ORDER BY m.score DESC, m.canonical_job_id
"""

# The candidate's own CV revision and the user it belongs to.
_CANDIDATE = """
    SELECT r.id::text, c.user_id::text
    FROM document_revisions r
    JOIN cv_documents c ON c.id = r.cv_document_id
    WHERE r.entity_kind = 'candidate' AND r.parsed_text IS NOT NULL
    ORDER BY r.created_at DESC
    LIMIT 1
"""


@dataclass(frozen=True)
class SampleResult:
    candidate_revision_id: str
    considered: int
    added: int
    skipped_existing: int
    coverage: dict[str, dict[str, int]]


@dataclass(frozen=True)
class EvaluationReport:
    candidate_revision_id: str
    metrics: RankingMetrics
    label_distribution: dict[int, int]
    progress: dict[str, int]

    def as_record(self) -> dict[str, Any]:
        return {
            "candidate_revision_id": self.candidate_revision_id,
            "metrics": self.metrics.as_record(),
            "label_distribution": {str(k): v for k, v in self.label_distribution.items()},
            "progress": self.progress,
        }


class EvaluationService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repository = EvaluationRepository(session)

    async def _default_candidate(self) -> tuple[uuid.UUID, uuid.UUID] | None:
        row = (await self._session.execute(text(_CANDIDATE))).first()
        if row is None:
            return None
        return uuid.UUID(row[0]), uuid.UUID(row[1])

    async def sample(self, size: int = 300, tier: str = "seed") -> SampleResult | None:
        """Add pairs worth judging to the set, from what the ranker has scored.

        20.1 calls sampling from what the system retrieved a legitimate
        shortcut — annotators spend their time where ranking decisions actually
        happen — on the condition that it is recorded. Every row carries the
        strategy and the score it had at sampling time, so a later reader can
        see that this set cannot answer questions about what was never scored.
        """
        candidate = await self._default_candidate()
        if candidate is None:
            return None
        candidate_revision_id, user_id = candidate

        rows = (await self._session.execute(text(_SCORED_PAIRS), {"user_id": user_id})).all()
        already = await self._repository.existing_job_ids(candidate_revision_id)

        pool = [
            Candidate(
                canonical_job_id=row[0],
                job_revision_id=row[1],
                score=float(row[2]),
                language=row[3],
            )
            for row in rows
            if uuid.UUID(row[0]) not in already
        ]
        chosen = stratified_sample(pool, size)

        added = await self._repository.add_pairs(
            [
                {
                    "id": uuid.uuid4(),
                    "candidate_revision_id": candidate_revision_id,
                    "canonical_job_id": uuid.UUID(item.canonical_job_id),
                    "job_revision_id": (
                        uuid.UUID(item.job_revision_id) if item.job_revision_id else None
                    ),
                    "tier": tier,
                    # Named so the shortcut stays visible: these pairs were
                    # chosen from what the ranker already returned.
                    "sampled_from": "ranked_stratified",
                    "system_score": item.score,
                }
                for item in chosen
            ]
        )
        return SampleResult(
            candidate_revision_id=str(candidate_revision_id),
            considered=len(rows),
            added=added,
            skipped_existing=len(already),
            coverage=coverage(chosen),
        )

    async def report(self) -> EvaluationReport | None:
        """Score the current ranking against the judgements that exist.

        Ranks come from the live `job_matches` ordering rather than from
        anything stored on the pair: the set is a fixed yardstick and the
        ranking is what changes, so re-running this after a model change is the
        whole point.
        """
        candidate = await self._default_candidate()
        if candidate is None:
            return None
        candidate_revision_id, user_id = candidate

        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT p.canonical_job_id::text, p.label,
                           row_number() OVER (ORDER BY m.score DESC, m.canonical_job_id) AS rank
                    FROM job_matches m
                    LEFT JOIN evaluation_pairs p
                      ON p.canonical_job_id = m.canonical_job_id
                     AND p.candidate_revision_id = :candidate
                    WHERE m.user_id = :user_id
                    """
                ),
                {"user_id": user_id, "candidate": candidate_revision_id},
            )
        ).all()

        judged = [
            Judged(rank=int(rank), label=int(label) if label is not None else None)
            for _, label, rank in rows
        ]
        return EvaluationReport(
            candidate_revision_id=str(candidate_revision_id),
            metrics=evaluate(judged),
            label_distribution=await self._repository.label_distribution(),
            progress=await self._repository.progress(),
        )
