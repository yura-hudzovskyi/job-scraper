"""Stage 2 — deterministic weighted scoring, and stage 3 — semantic similarity.

Weights are indicative defaults from docs/matching-engine.md; make them configurable
per user/search-profile rather than hardcoded once this grows real logic.
"""

from dataclasses import dataclass

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.candidates.skills import SkillRegistry
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.models import ScoreBreakdown
from app.integrations.ai.embeddings.base import EmbeddingProvider


@dataclass(frozen=True)
class ScoringWeights:
    skills: float = 0.30
    role: float = 0.15
    semantic_fit: float = 0.15
    experience: float = 0.10
    transferable_skills: float = 0.10
    salary: float = 0.05
    location: float = 0.05
    preferences: float = 0.05
    product_relevance: float = 0.05


class DeterministicScorer:
    def __init__(self, skill_registry: SkillRegistry, weights: ScoringWeights | None = None):
        self._skill_registry = skill_registry
        self._weights = weights or ScoringWeights()

    def score(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> ScoreBreakdown:
        """Combine skill/role/experience/transferable-skill/salary/location/preference
        components into a ScoreBreakdown, using self._weights."""
        raise NotImplementedError


class SemanticScorer:
    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding_provider = embedding_provider

    async def similarity(self, job: NormalizedJob, profile: CandidateProfile) -> float:
        """Cosine similarity between the candidate profile embedding and the job's
        requirements + responsibilities embedding."""
        raise NotImplementedError
