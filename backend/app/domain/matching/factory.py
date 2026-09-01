"""Builds a MatchingService wired to the configured embedding provider. Returns None
when no embedding provider is available — the deterministic pipeline can't run
without one (semantic_fit and skill matching both need it).
"""

import redis.asyncio as redis

from app.config.settings import Settings
from app.domain.matching.filters import HardFilterService
from app.domain.matching.llm_reranker import LlmReranker
from app.domain.matching.provenance import PipelineModels
from app.domain.matching.role_matching import RoleMatcher
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.domain.matching.skill_matching import SkillMatcher
from app.integrations.ai.embeddings.factory import (
    build_cross_encoder_provider,
    build_embedding_provider,
)
from app.integrations.ai.llm.budget import DailyCallBudget
from app.integrations.ai.llm.factory import build_job_llm_provider


def build_matching_service(settings: Settings) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    cross_encoder_provider = build_cross_encoder_provider(settings)

    # The "should I apply?" reranker (the only job-tier LLM call site now that
    # scoring is fully deterministic) uses Groq's free tier first (if
    # GROQ_API_KEY is set — fast enough for real volume), automatically falling
    # back to Gemini the instant Groq returns 429 (rate limit) — see
    # app/integrations/ai/llm/factory.py and fallback_provider.py. CV analysis and
    # preferences AI-fill are the separate, Gemini-first call sites
    # (build_quality_llm_provider).
    llm_provider = build_job_llm_provider(settings)

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
        SemanticScorer(embedding_provider, cross_encoder_provider, settings.cross_encoder_weight),
        SkillMatcher(embedding_provider),
        RoleMatcher(embedding_provider),
        llm_reranker=llm_reranker,
        # The factory is the only layer that knows which models Settings selected;
        # the service just records them on every result it produces.
        models=PipelineModels(
            embedding=settings.embedding_model,
            cross_encoder=settings.cross_encoder_model,
        ),
    )
