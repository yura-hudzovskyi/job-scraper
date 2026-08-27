"""Orchestrates the full matching pipeline: filters -> deterministic score ->
semantic score -> (optional) LLM rerank for the shortlist. See docs/matching-engine.md.

This is the only entry point other modules should call — it composes
HardFilterService, DeterministicScorer, SemanticScorer and an LLMProvider, none of
which should be called directly from services/ or the API layer.
"""

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.filters import HardFilterService
from app.domain.matching.models import JobMatch
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.integrations.ai.llm.base import LLMProvider


class MatchingService:
    def __init__(
        self,
        hard_filters: HardFilterService,
        deterministic_scorer: DeterministicScorer,
        semantic_scorer: SemanticScorer,
        llm_provider: LLMProvider | None = None,
    ):
        self._hard_filters = hard_filters
        self._deterministic_scorer = deterministic_scorer
        self._semantic_scorer = semantic_scorer
        self._llm_provider = llm_provider

    async def evaluate(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> JobMatch:
        """Run the full pipeline for a single job and return an explainable JobMatch."""
        raise NotImplementedError

    async def rerank_shortlist(self, matches: list[JobMatch]) -> list[JobMatch]:
        """Send the top-N deterministic+semantic matches to the LLM for reranking and
        gap analysis. Only ever called on an already-filtered shortlist."""
        raise NotImplementedError

    async def should_i_apply(self, match: JobMatch) -> str:
        """Structured, explainable apply/skip recommendation for a single match."""
        raise NotImplementedError
