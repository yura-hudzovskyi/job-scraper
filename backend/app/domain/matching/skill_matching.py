"""Comparing what a vacancy asks for against what a candidate has — see
docs/ai-pipeline-v3.md (E2).

The comparison happens in that order, ontology first and embeddings second,
because the two answer different questions:

1. **The ontology** knows that "Postgres" and "PostgreSQL" are one skill, and
   that TypeScript is evidence for JavaScript but not the reverse. Those are
   facts, and they produce an explanation a user can check.
2. **Embeddings** cover everything the ontology has never heard of. Similarity is
   a good "are these about the same thing" signal and a poor "is this the same
   requirement" one, so it can confirm a match or mark something partial — it
   never invents an equivalence the ontology would have denied.

The outcome per requirement is a type, not a bool, because "the posting never
said whether this is needed" and "the candidate doesn't have it" are different
findings and only one of them is a gap. Reporting an unknown as a missing skill
is the specific failure this shape exists to prevent.

The three scores keep the formulas the deterministic pipeline has always used —
only the "does the candidate have this" test got better.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.jobs.models import NormalizedJobSkill, RequirementType
from app.domain.matching.similarity import best_similarity, cosine_similarity
from app.domain.skills.normalizer import dedupe_key, normalize_skill
from app.domain.skills.ontology import by_id
from app.integrations.ai.embeddings.base import EmbeddingProvider

DEFAULT_MATCH_THRESHOLD = 0.75
# Below the match threshold but clearly related: enough to say "adjacent
# experience", not enough to call the requirement satisfied.
DEFAULT_PARTIAL_THRESHOLD = 0.55


class SkillOutcome(StrEnum):
    MATCHED = "matched"  # the candidate has this skill
    MATCHED_EQUIVALENT = "matched_equivalent"  # implied by something they do have
    PARTIAL = "partial"  # adjacent experience, not the same skill
    MISSING = "missing"
    # The posting mentions it without saying whether it is needed. Never a gap.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SkillFinding:
    """One requirement and what the candidate has against it."""

    name: str
    requirement: RequirementType
    outcome: SkillOutcome
    canonical_id: str | None = None
    # Which of the candidate's skills satisfied (or nearly satisfied) it.
    matched_by: str | None = None
    similarity: float | None = None
    # The posting's own words behind this requirement, when extraction captured
    # them — see JobSkillExtractionService.
    evidence: str | None = None

    @property
    def satisfied(self) -> bool:
        return self.outcome in (SkillOutcome.MATCHED, SkillOutcome.MATCHED_EQUIVALENT)

    @property
    def is_gap(self) -> bool:
        """Only a real requirement the candidate demonstrably lacks. An unknown
        is not a gap, and neither is adjacent experience."""
        return self.outcome is SkillOutcome.MISSING


@dataclass(frozen=True)
class SkillAssessment:
    skills_score: float
    transferable_score: float
    preferences_score: float
    strengths: list[str] = field(default_factory=list)
    gaps: list[tuple[str, bool]] = field(default_factory=list)  # (skill name, is_required)
    findings: list[SkillFinding] = field(default_factory=list)

    @property
    def required_coverage(self) -> float:
        """Share of the *required* skills the candidate satisfies, 0-1. None of
        the unknowns count either way — they were never established as
        requirements."""
        required = [
            finding
            for finding in self.findings
            if finding.requirement
            in (RequirementType.REQUIRED_EXPLICIT, RequirementType.REQUIRED_INFERRED)
        ]
        if not required:
            return 1.0
        return sum(1 for finding in required if finding.satisfied) / len(required)


_NEUTRAL = SkillAssessment(skills_score=100.0, transferable_score=100.0, preferences_score=100.0)


def _implied_ids(candidate_ids: set[str]) -> set[str]:
    """Everything the candidate's known skills are evidence *for*. Directed on
    purpose: React Native does not imply React, and TypeScript implies JavaScript
    but not the other way round."""
    implied: set[str] = set()
    for skill_id in candidate_ids:
        skill = by_id(skill_id)
        if skill is not None:
            implied.update(skill.implies)
    return implied


def _related_ids(candidate_ids: set[str]) -> set[str]:
    related: set[str] = set()
    for skill_id in candidate_ids:
        skill = by_id(skill_id)
        if skill is not None:
            related.update(skill.related)
    return related


class SkillMatcher:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        partial_threshold: float = DEFAULT_PARTIAL_THRESHOLD,
    ):
        self._embedding_provider = embedding_provider
        self._match_threshold = match_threshold
        self._partial_threshold = partial_threshold

    async def assess(
        self,
        job_skills: Sequence[NormalizedJobSkill],
        candidate_skills: Sequence[str],
        preferred_stack: Sequence[str],
        acceptable_stack: Sequence[str],
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

        candidate_normalized = [normalize_skill(name) for name in candidate_skills]
        candidate_ids = {
            normalized.canonical_id for normalized in candidate_normalized if normalized.canonical_id
        }
        candidate_keys = {dedupe_key(name): name for name in candidate_skills}
        implied = _implied_ids(candidate_ids)  # type: ignore[arg-type]
        related = _related_ids(candidate_ids)  # type: ignore[arg-type]

        findings: list[SkillFinding] = []
        preference_weights: list[float] = []
        for skill, job_vector in zip(job_skills, job_vectors, strict=True):
            findings.append(
                self._assess_one(
                    skill,
                    job_vector,
                    candidate_skills=list(candidate_skills),
                    candidate_vectors=candidate_vectors,
                    candidate_ids=candidate_ids,  # type: ignore[arg-type]
                    candidate_keys=candidate_keys,
                    implied=implied,
                    related=related,
                )
            )
            preference_weights.append(
                self._preference_weight(job_vector, preferred_vectors, acceptable_vectors)
            )

        strengths = [finding.name for finding in findings if finding.satisfied]
        # Everything the candidate doesn't actually have, required or not —
        # including the ones they only have adjacent experience for, which are
        # worth showing ("Kubernetes: you have Docker"). Unknowns stay out: the
        # posting never established them as requirements.
        gaps = [
            (
                finding.name,
                finding.requirement
                in (RequirementType.REQUIRED_EXPLICIT, RequirementType.REQUIRED_INFERRED),
            )
            for finding in findings
            if not finding.satisfied and finding.outcome is not SkillOutcome.UNKNOWN
        ]
        transfer_values = [
            finding.similarity or 0.0 for finding in findings if not finding.satisfied
        ]

        return SkillAssessment(
            skills_score=len(strengths) / len(findings) * 100,
            transferable_score=(
                100.0 if not transfer_values else sum(transfer_values) / len(transfer_values) * 100
            ),
            preferences_score=(
                100.0
                if not preferred_stack and not acceptable_stack
                else sum(preference_weights) / len(preference_weights) * 100
            ),
            strengths=strengths,
            gaps=gaps,
            findings=findings,
        )

    def _assess_one(
        self,
        skill: NormalizedJobSkill,
        job_vector: list[float],
        candidate_skills: list[str],
        candidate_vectors: list[list[float]],
        candidate_ids: set[str],
        candidate_keys: dict[str, str],
        implied: set[str],
        related: set[str],
    ) -> SkillFinding:
        canonical_id = skill.canonical_id or normalize_skill(skill.name).canonical_id

        def finding(outcome: SkillOutcome, matched_by: str | None, similarity: float | None):
            return SkillFinding(
                name=skill.name,
                requirement=skill.requirement,
                outcome=outcome,
                canonical_id=canonical_id,
                matched_by=matched_by,
                similarity=similarity,
                evidence=skill.evidence,
            )

        # 1. The same skill, however either side spelled it.
        if canonical_id is not None and canonical_id in candidate_ids:
            return finding(SkillOutcome.MATCHED, canonical_id, 1.0)
        key = dedupe_key(skill.name)
        if key in candidate_keys:
            return finding(SkillOutcome.MATCHED, candidate_keys[key], 1.0)

        # 2. Something the candidate has is evidence for it.
        if canonical_id is not None and canonical_id in implied:
            return finding(SkillOutcome.MATCHED_EQUIVALENT, canonical_id, None)

        # 3. Whatever the ontology doesn't know, similarity might.
        similarity = best_similarity(job_vector, candidate_vectors)
        nearest = self._nearest(job_vector, candidate_vectors, candidate_skills)
        if similarity >= self._match_threshold:
            return finding(SkillOutcome.MATCHED, nearest, similarity)

        if (canonical_id is not None and canonical_id in related) or (
            similarity >= self._partial_threshold
        ):
            return finding(SkillOutcome.PARTIAL, nearest, similarity)

        # 4. Not held — but only a stated requirement can be a gap.
        if skill.requirement in (RequirementType.UNKNOWN, RequirementType.CONTEXT):
            return finding(SkillOutcome.UNKNOWN, None, similarity)
        return finding(SkillOutcome.MISSING, None, similarity)

    def _nearest(
        self,
        job_vector: list[float],
        candidate_vectors: list[list[float]],
        candidate_skills: list[str],
    ) -> str | None:
        if not candidate_vectors:
            return None
        scores = [cosine_similarity(job_vector, vector) for vector in candidate_vectors]
        best = max(range(len(scores)), key=lambda index: scores[index])
        return candidate_skills[best]

    def _preference_weight(
        self,
        job_vector: list[float],
        preferred_vectors: list[list[float]],
        acceptable_vectors: list[list[float]],
    ) -> float:
        if best_similarity(job_vector, preferred_vectors) >= self._match_threshold:
            return 1.0
        if best_similarity(job_vector, acceptable_vectors) >= self._match_threshold:
            return 0.6
        return 0.0
