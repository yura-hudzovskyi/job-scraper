"""Builds a MatchingService wired to the default skill registry and the configured
embedding provider. Returns None when no embedding provider is available — unlike CV
analysis, matching can't degrade gracefully without one (semantic_fit needs it).
"""

from app.config.settings import Settings
from app.domain.candidates.skill_data import build_default_skill_registry
from app.domain.matching.filters import HardFilterService
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.integrations.ai.embeddings.factory import build_embedding_provider


def build_matching_service(settings: Settings) -> MatchingService | None:
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        return None

    registry = build_default_skill_registry()
    return MatchingService(
        HardFilterService(),
        DeterministicScorer(registry),
        SemanticScorer(embedding_provider),
    )
