"""Builds a MatchingService wired to the configured embedding provider. Returns None
when no embedding provider is available — the deterministic fallback can't degrade
gracefully without one (semantic_fit and skill matching both need it), even though
the primary AI-matching path itself doesn't touch embeddings.
"""

import redis.asyncio as redis

from app.config.settings import Settings
from app.domain.matching.ai_matcher import AiMatcher
from app.domain.matching.filters import HardFilterService
from app.domain.matching.llm_reranker import LlmReranker
from app.domain.matching.role_matching import RoleMatcher
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.domain.matching.skill_matching import SkillMatcher
from app.integrations.ai.embeddings.factory import build_embedding_provider
from app.integrations.ai.llm.budget import DailyCallBudget
from app.integrations.ai.llm.factory import (
    build_configured_llm_provider,
    build_quality_llm_provider,
)


def build_matching_service(
    settings: Settings, llm_model_override: str | None = None
) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    # Primary path — see app/domain/matching/ai_matcher.py. Deliberately not routed
    # through build_quality_llm_provider's Gemini-first wiring: this runs once per
    # (job, user) — every scored job, not just APPLY-tier ones the reranker below
    # gates on — so it'd blow through Gemini's free-tier quota fast. Whatever
    # llm_provider is configured (Ollama by default) instead, same call-volume
    # reasoning as build_bulk_llm_provider's job-skill-extraction use.
    ai_matcher = None
    matching_llm_provider = build_configured_llm_provider(settings, llm_model_override)
    if matching_llm_provider is not None:
        ai_matcher = AiMatcher(matching_llm_provider)

    llm_reranker = None
    llm_provider = build_quality_llm_provider(settings, llm_model_override)
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
        ai_matcher=ai_matcher,
    )
