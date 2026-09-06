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
from app.domain.matching.documents import job_document, rerank_query, text_hash
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
    # How many of those scores came back from the cache rather than the
    # provider. The two together are what say whether a run cost anything: after
    # the first full pass over a settled corpus, `reranked` is the whole eligible
    # set and `rerank_reused` is nearly all of it.
    rerank_reused: int = 0
    rerank_failed: bool = False
    written: int = 0
    notify: list[str] = field(default_factory=list)
    recommendations: dict[str, int] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return self.skipped_reason is None


def _positions(relevance: dict[uuid.UUID, float]) -> dict[uuid.UUID, int]:
    """Where the reranker put each vacancy, best first. Ties break on id so a
    re-run of the same inputs produces the same positions."""
    ranked = sorted(relevance.items(), key=lambda item: (-item[1], str(item[0])))
    return {
        canonical_job_id: position for position, (canonical_job_id, _) in enumerate(ranked, start=1)
    }


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
        self._embedding_service = EmbeddingService(embedding_repository, job_repository, voyage)

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
            return MatchingResult(
                user_id=str(user_id), skipped_reason="the CV could not be embedded"
            )

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

        relevance, positions, rerank_failed, reused = await self._rerank(
            user_id, profile_text, eligible, jobs
        )
        query_hash = text_hash(rerank_query(profile_text))

        for canonical_job_id in eligible:
            job_relevance = relevance.get(canonical_job_id)
            score = combine(similarity[canonical_job_id], job_relevance, self._config.rerank_weight)
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
                    rerank_query_hash=query_hash if job_relevance is not None else None,
                    rerank_document_hash=(
                        text_hash(job_document(jobs[canonical_job_id]))
                        if job_relevance is not None
                        else None
                    ),
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
            rerank_reused=reused,
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
        user_id: uuid.UUID,
        profile_text: str,
        eligible: list[uuid.UUID],
        jobs: dict[uuid.UUID, NormalizedJob],
    ) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, int], bool, int]:
        """Relevance for every eligible vacancy, plus each one's rank.

        Everything, not a top slice: the reranker is the only stage that reads a
        CV and a vacancy together, so a cap on it caps the quality of the
        ranking rather than only its cost. `rerank_top_k` survives as a safety
        valve, with 0 meaning no limit.

        What keeps that affordable is the cache, not the cap. A score depends on
        the CV, the vacancy text and the model, and none of those move between
        most runs — so a settled corpus pays for almost nothing, and 24.0
        invariant 4 is satisfied where it belongs.

        A failure is reported, not hidden: everything keeps its embedding-only
        score and the run says the reranker did not contribute. That mattered
        more than it should have — a batch too large for the provider's token
        limit failed on every run for weeks, and the only visible symptom was a
        ranking quietly worse than the configuration described.
        """
        limit = self._config.rerank_top_k or len(eligible)
        batch = eligible[: min(limit, len(eligible))]
        if not batch:
            return {}, {}, False, 0

        query = rerank_query(profile_text)
        query_hash = text_hash(query)
        cached = await self._matches.stored_relevance(user_id, self._voyage.rerank_model)

        relevance: dict[uuid.UUID, float] = {}
        pending: list[uuid.UUID] = []
        for canonical_job_id in batch:
            document_hash = text_hash(job_document(jobs[canonical_job_id]))
            known = cached.get(canonical_job_id)
            if known is not None and known[1] == query_hash and known[2] == document_hash:
                relevance[canonical_job_id] = known[0]
            else:
                pending.append(canonical_job_id)

        reused = len(relevance)
        if pending:
            documents = [job_document(jobs[canonical_job_id]) for canonical_job_id in pending]
            try:
                scored = await self._voyage.rerank(query, documents)
            except Exception:
                logger.warning("rerank failed for %d vacancies", len(pending), exc_info=True)
                # Keep what earlier batches returned: it was paid for and it is
                # correct, and discarding it turns a partial outage into a total
                # one. The run still reports that the reranker failed.
                return relevance, _positions(relevance), True, reused

            # Scores come back keyed by position in `documents`, so a batch that
            # never ran is simply absent. Those vacancies keep their
            # embedding-only score instead of a fabricated zero, which would
            # rank them below everything the reranker actively disliked.
            relevance.update({pending[index]: score for index, score in scored.items()})
            if len(scored) != len(pending):
                logger.warning("rerank scored %d of %d vacancies", len(scored), len(pending))
                return relevance, _positions(relevance), True, reused

        logger.info("reranked %d vacancies (%d reused from cache)", len(pending), reused)
        return relevance, _positions(relevance), False, reused
