"""Stage 2 — deterministic weighted scoring, and stage 3 — semantic similarity.

Weights are indicative defaults from docs/matching-engine.md; make them configurable
per user/search-profile rather than hardcoded once this grows real logic.

Skill-aware scoring (skills/transferable_skills/preferences) lives in
skill_matching.py's SkillMatcher, and role scoring lives in role_matching.py's
RoleMatcher — neither lives here, since both need an embedding call, so
MatchingService computes them separately and merges everything into
DeterministicScore before calling overall(). This class only covers the genuinely
synchronous, registry-free components: experience/salary/location.
"""

from dataclasses import dataclass

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import NormalizedJob
from app.domain.matching.similarity import cosine_similarity
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
    preferences: float = 0.10


@dataclass(frozen=True)
class DeterministicScore:
    """Everything ScoreBreakdown needs except semantic_fit (computed separately by
    SemanticScorer) and skills/transferable_skills/preferences (computed separately
    by SkillMatcher). See MatchingService, which merges all three into one instance
    before calling overall()."""

    skills: float
    role: float
    experience: float
    transferable_skills: float
    salary: float
    location: float
    preferences: float


class DeterministicScorer:
    def __init__(self, weights: ScoringWeights | None = None):
        self._weights = weights or ScoringWeights()

    def score(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> tuple[float, float, float]:
        """Returns (experience, salary, location) — MatchingService merges these
        with RoleMatcher's and SkillMatcher's output to build the full
        DeterministicScore."""
        return (
            self._experience_score(job, profile),
            self._salary_score(job, preferences),
            self._location_score(job, preferences),
        )

    def overall(
        self,
        deterministic: DeterministicScore,
        semantic_fit: float,
        skills_available: bool = True,
    ) -> float:
        """Weighted combination of every component, including the separately-computed
        semantic_fit. Result is 0-100.

        `skills_available` must be False when the job had no extracted skills at all
        (job.skills was empty) — SkillMatcher then reports skills/transferable/
        preferences as a fabricated "neutral" 100 (nothing required, so nothing
        missing), which is not the same as "this job is a good fit". Folding that
        100 straight into the weighted sum (50% of total weight) made every such job
        look like a near-perfect match regardless of role or semantic fit — e.g. an
        "Account Manager" posting scoring ~85% against a developer profile. When
        there's no real skill signal, drop those three components from the average
        instead of crediting them, and rescale the remaining weights to sum to 1.
        """
        w = self._weights
        if not skills_available:
            remaining = w.role + w.semantic_fit + w.experience + w.salary + w.location
            return (
                deterministic.role * w.role
                + semantic_fit * w.semantic_fit
                + deterministic.experience * w.experience
                + deterministic.salary * w.salary
                + deterministic.location * w.location
            ) / remaining

        return (
            deterministic.skills * w.skills
            + deterministic.role * w.role
            + semantic_fit * w.semantic_fit
            + deterministic.experience * w.experience
            + deterministic.transferable_skills * w.transferable_skills
            + deterministic.salary * w.salary
            + deterministic.location * w.location
            + deterministic.preferences * w.preferences
        )

    def _experience_score(self, job: NormalizedJob, profile: CandidateProfile) -> float:
        required = job.required_experience_years
        if required is None or required <= 0 or profile.experience_years >= required:
            return 100.0
        return max(0.0, profile.experience_years / required * 100)

    def _salary_score(self, job: NormalizedJob, preferences: UserPreference) -> float:
        desired = preferences.desired_salary_usd
        if desired is None or job.salary is None or job.salary.currency != "USD":
            return 100.0
        reference = job.salary.max if job.salary.max is not None else job.salary.min
        if reference is None or reference >= desired:
            return 100.0
        return max(0.0, reference / desired * 100)

    def _location_score(self, job: NormalizedJob, preferences: UserPreference) -> float:
        if not preferences.locations:
            return 100.0
        job_places = [place.lower() for place in (*job.location.countries, *job.location.cities)]
        if not job_places:
            return 100.0 if job.location.remote else 60.0
        candidate_places = [loc.lower() for loc in preferences.locations]
        matches = any(
            candidate in place or place in candidate
            for place in job_places
            for candidate in candidate_places
        )
        return 100.0 if matches else 50.0


class SemanticScorer:
    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding_provider = embedding_provider

    async def similarity(self, job: NormalizedJob, profile: CandidateProfile) -> float:
        """Cosine similarity (0-1) between the candidate profile embedding and the
        job's requirements + responsibilities embedding."""
        profile_text = _profile_text(profile)
        job_text = f"{job.title}\n{job.description}"
        [profile_vector, job_vector] = await self._embedding_provider.embed(
            [profile_text, job_text]
        )
        return cosine_similarity(profile_vector, job_vector)


def _profile_text(profile: CandidateProfile) -> str:
    parts = [
        " ".join(profile.roles),
        " ".join(skill.name for skill in profile.skills),
        " ".join(f"{entry.title} {entry.description}" for entry in profile.experience),
        " ".join(profile.achievements),
        " ".join(profile.domains),
    ]
    return "\n".join(part for part in parts if part)
