"""One matching pass for one user — the whole of it, in one readable sequence.

    embed the CV  ->  vector search over every vacancy  ->  hard filters
                  ->  rerank the top K  ->  blend  ->  save  ->  notify

There is no LLM anywhere in this, no extracted skill lists, no per-facet weights
and no confidence model. A match is two numbers and the weight between them, and
every one of the three is stored on the row so the UI can show the arithmetic
rather than a verdict.

Two ordering decisions carry the design:

- **Filters run after the search, before the rerank.** The search is one indexed
  query over vectors the app already has, so filtering first would save nothing;
  the rerank is the part that costs money per document, so nothing the user has
  ruled out ever reaches it.
- **Ineligible vacancies are still written.** A job missing from the list because
  of a rule the user set is a different thing from a job that was never seen, and
  storing the reason is what lets the UI tell them apart.
"""

import logging
import uuid
from dataclasses import dataclass, field

from app.domain.candidates.models import UserPreference
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.documents import job_document, rerank_query
from app.domain.matching.filters import HardFilterService
from app.domain.matching.models import JobMatch, Recommendation
from app.domain.matching.scoring import combine, recommend
from app.domain.pipeline_config import PipelineConfig
from app.integrations.voyage import VoyageClient
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchingResult:
    """What one pass did, in the same order it did it. Every count here is
    rendered on the System page, so a run that produced nothing still explains
    itself."""

    user_id: str
    skipped_reason: str | None = None
    retrieved: int = 0
    eligible: int = 0
    filtered_out: int = 0
    reranked: int = 0
    rerank_failed: bool = False
    written: int = 0
    notify: list[str] = field(default_factory=list)
    recommendations: dict[str, int] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return self.skipped_reason is None


class MatchingService:
    def __init__(
        self,
        config: PipelineConfig,
        voyage: VoyageClient,
        candidate_repository: CandidateRepository,
        job_repository: JobRepository,
        embedding_repository: EmbeddingRepository,
        match_repository: MatchRepository,
        filters: HardFilterService | None = None,
    ):
        self._config = config
        self._voyage = voyage
        self._candidates = candidate_repository
        self._jobs = job_repository
        self._embeddings = embedding_repository
        self._matches = match_repository
        self._filters = filters or HardFilterService()
        self._embedding_service = EmbeddingService(
            embedding_repository, job_repository, voyage
        )

    async def run_for_user(self, user_id: uuid.UUID) -> MatchingResult:
        cv = await self._candidates.get_active_cv(user_id)
        if cv is None:
            return MatchingResult(user_id=str(user_id), skipped_reason="no CV uploaded")
        if not cv.raw_text.strip():
            return MatchingResult(
                user_id=str(user_id), skipped_reason="the uploaded CV has no readable text"
            )

        preferences = await self._candidates.get_preferences(user_id)
        profile_text, _ = await self._embedding_service.index_profile(
            user_id, cv.raw_text, preferences
        )
        query_vector = await self._embedding_service.get_profile_vector(user_id)
        if query_vector is None:
            return MatchingResult(user_id=str(user_id), skipped_reason="the CV could not be embedded")

        candidates = await self._embeddings.search(
            self._voyage.embedding_model, query_vector, self._config.retrieval_limit
        )
        if not candidates:
            return MatchingResult(
                user_id=str(user_id),
                skipped_reason="no vacancies are embedded yet under the configured model",
            )

        similarity = {candidate.document_id: candidate.similarity for candidate in candidates}
        jobs = await self._jobs.list_normalized_jobs_for_canonical(list(similarity))

        eligible: list[uuid.UUID] = []
        matches: list[JobMatch] = []
        for canonical_job_id, job_similarity in similarity.items():
            job = jobs.get(canonical_job_id)
            if job is None:
                continue
            verdict = self._filters.evaluate(
                job, preferences or UserPreference(user_id=str(user_id))
            )
            if verdict.eligible:
                eligible.append(canonical_job_id)
            else:
                matches.append(
                    JobMatch(
                        user_id=str(user_id),
                        canonical_job_id=str(canonical_job_id),
                        eligible=False,
                        filter_reasons=verdict.reasons,
                        similarity=job_similarity,
                        recommendation=Recommendation.SKIP,
                        embedding_model=self._voyage.embedding_model,
                    )
                )

        relevance, positions, rerank_failed = await self._rerank(profile_text, eligible, jobs)

        for canonical_job_id in eligible:
            job_relevance = relevance.get(canonical_job_id)
            score = combine(
                similarity[canonical_job_id], job_relevance, self._config.rerank_weight
            )
            matches.append(
                JobMatch(
                    user_id=str(user_id),
                    canonical_job_id=str(canonical_job_id),
                    eligible=True,
                    score=score,
                    similarity=similarity[canonical_job_id],
                    relevance=job_relevance,
                    rerank_position=positions.get(canonical_job_id),
                    recommendation=recommend(
                        score, self._config.apply_threshold, self._config.consider_threshold
                    ),
                    embedding_model=self._voyage.embedding_model,
                    rerank_model=self._voyage.rerank_model if job_relevance is not None else None,
                    rerank_weight=self._config.rerank_weight if job_relevance is not None else None,
                )
            )

        written = await self._matches.upsert_many(matches)

        recommendations: dict[str, int] = {}
        for match in matches:
            recommendations[match.recommendation.value] = (
                recommendations.get(match.recommendation.value, 0) + 1
            )

        return MatchingResult(
            user_id=str(user_id),
            retrieved=len(candidates),
            eligible=len(eligible),
            filtered_out=len(matches) - len(eligible),
            reranked=len(relevance),
            rerank_failed=rerank_failed,
            written=written,
            # Only APPLY matches are worth interrupting someone for; the
            # notification policy has the final say on whether one is sent.
            notify=[
                match.canonical_job_id
                for match in matches
                if match.recommendation is Recommendation.APPLY
            ],
            recommendations=recommendations,
        )

    async def _rerank(
        self,
        profile_text: str,
        eligible: list[uuid.UUID],
        jobs: dict[uuid.UUID, NormalizedJob],
    ) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, int], bool]:
        """Relevance for the top K by similarity, plus each one's rank. A failure
        is reported, not hidden: everything keeps its embedding-only score and the
        run says the reranker didn't contribute."""
        top_k = min(self._config.rerank_top_k, len(eligible))
        if top_k <= 0:
            return {}, {}, False

        batch = eligible[:top_k]
        documents = [job_document(jobs[canonical_job_id]) for canonical_job_id in batch]
        try:
            scores = await self._voyage.rerank(rerank_query(profile_text), documents)
        except Exception:
            logger.warning("rerank failed for a batch of %d vacancies", len(batch), exc_info=True)
            return {}, {}, True
        if len(scores) != len(batch):
            logger.warning(
                "rerank returned %d scores for %d vacancies — ignoring them",
                len(scores),
                len(batch),
            )
            return {}, {}, True

        relevance = dict(zip(batch, scores, strict=True))
        ranked = sorted(relevance.items(), key=lambda item: (-item[1], str(item[0])))
        positions = {
            canonical_job_id: position
            for position, (canonical_job_id, _) in enumerate(ranked, start=1)
        }
        return relevance, positions, False
