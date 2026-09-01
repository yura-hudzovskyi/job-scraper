"""Builds a MatchingService wired to the configured embedding provider. Returns None
when no embedding provider is available — the deterministic pipeline can't run
without one (semantic_fit and skill matching both need it).
"""

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
from app.integrations.ai.llm.factory import build_llm_router
from app.integrations.ai.routing.router import Capability


def build_matching_service(settings: Settings) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    cross_encoder_provider = build_cross_encoder_provider(settings)

    # The "should I apply?" reranker (the only job-tier LLM call site now that
    # scoring is fully deterministic) runs on the MATCH_ENRICHMENT capability,
    # with that capability's own daily budget and provider order — see
    # app/integrations/ai/routing/policy.py. The router decides which leg serves
    # the call and when there is no capacity left; the reranker just asks.
    llm_provider = build_llm_router(Capability.MATCH_ENRICHMENT, settings)
    llm_reranker = LlmReranker(llm_provider) if llm_provider is not None else None

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
