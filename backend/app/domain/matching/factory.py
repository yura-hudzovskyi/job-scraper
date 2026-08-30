"""Builds a MatchingService wired to the configured embedding provider. Returns None
when no embedding provider is available — matching can't degrade gracefully without
one (semantic_fit and skill matching both need it).
"""

import redis.asyncio as redis

from app.config.settings import Settings
from app.domain.matching.filters import HardFilterService
from app.domain.matching.llm_reranker import LlmReranker
from app.domain.matching.role_matching import RoleMatcher
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.domain.matching.skill_matching import SkillMatcher
from app.integrations.ai.embeddings.factory import build_embedding_provider
from app.integrations.ai.llm.budget import DailyCallBudget
from app.integrations.ai.llm.factory import build_quality_llm_provider


def build_matching_service(settings: Settings) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    llm_reranker = None
    llm_provider = build_quality_llm_provider(settings)
    if llm_provider is not None:
        budget = DailyCallBudget(
            redis.from_url(settings.redis_url),
            key_prefix="llm_rerank",
            daily_limit=settings.llm_rerank_daily_limit,
        )
        llm_reranker = LlmReranker(llm_provider, budget)

    return MatchingService(
        HardFilterService(),
        DeterministicScorer(),
        SemanticScorer(embedding_provider),
        SkillMatcher(embedding_provider),
        RoleMatcher(embedding_provider),
        llm_reranker=llm_reranker,
    )
