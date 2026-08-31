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
from app.integrations.ai.llm.factory import build_quality_llm_provider


def build_matching_service(
    settings: Settings, llm_model_override: str | None = None
) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    # Both the primary AI-matching path (ai_matcher.py) and the "should I apply?"
    # reranker share one quality provider: Gemini's free tier first (if
    # GEMINI_API_KEY is set), automatically falling back to Ollama the instant
    # Gemini returns 429 (quota exceeded) — see
    # app/integrations/ai/llm/fallback_provider.py. That fallback is what actually
    # implements "Gemini by default, local Ollama once we hit limits" for every AI
    # call site here, with no extra plumbing needed.
    llm_provider = build_quality_llm_provider(settings, llm_model_override)

    ai_matcher = AiMatcher(llm_provider) if llm_provider is not None else None

    llm_reranker = None
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
