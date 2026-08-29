"""Builds a MatchingService wired to the configured embedding provider. Returns None
when no embedding provider is available — matching can't degrade gracefully without
one (semantic_fit and skill matching both need it).
"""

from app.config.settings import Settings
from app.domain.matching.filters import HardFilterService
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.domain.matching.skill_matching import SkillMatcher
from app.integrations.ai.embeddings.factory import build_embedding_provider


def build_matching_service(settings: Settings) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    return MatchingService(
        HardFilterService(),
        DeterministicScorer(),
        SemanticScorer(embedding_provider),
        SkillMatcher(embedding_provider),
    )
