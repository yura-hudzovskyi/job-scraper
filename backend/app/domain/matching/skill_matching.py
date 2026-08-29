"""Embedding-based skill matching — replaces registry/alias lookups with cosine
similarity between skill-name embeddings, so any vocabulary works without a
hand-maintained skill list or transferability table. See
app/integrations/ai/embeddings/base.py and docs/matching-engine.md.

Ports the exact scoring formulas the old registry-based DeterministicScorer used
(skills_score = matched/total, transferable_score = mean best-similarity for gaps,
preferences_score = weighted preferred/acceptable stack coverage) — only the "is
skill A the same as skill B" identity test changed, not the weighting logic.
"""

from dataclasses import dataclass, field

from app.domain.jobs.models import NormalizedJobSkill
from app.domain.matching.similarity import cosine_similarity
from app.integrations.ai.embeddings.base import EmbeddingProvider

DEFAULT_MATCH_THRESHOLD = 0.75


@dataclass(frozen=True)
class SkillAssessment:
    skills_score: float
    transferable_score: float
    preferences_score: float
    strengths: list[str] = field(default_factory=list)
    gaps: list[tuple[str, bool]] = field(default_factory=list)  # (skill name, is_required)


_NEUTRAL = SkillAssessment(skills_score=100.0, transferable_score=100.0, preferences_score=100.0)


class SkillMatcher:
    def __init__(
        self, embedding_provider: EmbeddingProvider, match_threshold: float = DEFAULT_MATCH_THRESHOLD
    ):
        self._embedding_provider = embedding_provider
        self._match_threshold = match_threshold

    async def assess(
        self,
        job_skills: list[NormalizedJobSkill],
        candidate_skills: list[str],
        preferred_stack: list[str],
        acceptable_stack: list[str],
    ) -> SkillAssessment:
        if not job_skills:
            return _NEUTRAL

        job_names = [skill.name for skill in job_skills]
        vectors = await self._embedding_provider.embed(
            [*job_names, *candidate_skills, *preferred_stack, *acceptable_stack]
        )
        job_vectors = vectors[: len(job_names)]
        candidate_vectors = vectors[len(job_names) : len(job_names) + len(candidate_skills)]
        preferred_vectors = vectors[
            len(job_names) + len(candidate_skills) : len(job_names)
            + len(candidate_skills)
            + len(preferred_stack)
        ]
        acceptable_vectors = vectors[len(job_names) + len(candidate_skills) + len(preferred_stack) :]

        strengths: list[str] = []
        gaps: list[tuple[str, bool]] = []
        transfer_values: list[float] = []
        preference_weights: list[float] = []

        for skill, job_vector in zip(job_skills, job_vectors, strict=True):
            best_candidate_similarity = _best_similarity(job_vector, candidate_vectors)
            if best_candidate_similarity >= self._match_threshold:
                strengths.append(skill.name)
            else:
                gaps.append((skill.name, skill.required))
                transfer_values.append(best_candidate_similarity)

            preference_weights.append(
                self._preference_weight(job_vector, preferred_vectors, acceptable_vectors)
            )

        skills_score = len(strengths) / len(job_skills) * 100
        transferable_score = (
            100.0 if not transfer_values else sum(transfer_values) / len(transfer_values) * 100
        )
        preferences_score = (
            100.0
            if not preferred_stack and not acceptable_stack
            else sum(preference_weights) / len(preference_weights) * 100
        )

        return SkillAssessment(
            skills_score=skills_score,
            transferable_score=transferable_score,
            preferences_score=preferences_score,
            strengths=strengths,
            gaps=gaps,
        )

    def _preference_weight(
        self,
        job_vector: list[float],
        preferred_vectors: list[list[float]],
        acceptable_vectors: list[list[float]],
    ) -> float:
        if _best_similarity(job_vector, preferred_vectors) >= self._match_threshold:
            return 1.0
        if _best_similarity(job_vector, acceptable_vectors) >= self._match_threshold:
            return 0.6
        return 0.0


def _best_similarity(target: list[float], candidates: list[list[float]]) -> float:
    if not candidates:
        return 0.0
    return max(cosine_similarity(target, candidate) for candidate in candidates)
