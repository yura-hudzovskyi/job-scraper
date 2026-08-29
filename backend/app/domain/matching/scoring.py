"""Stage 2 — deterministic weighted scoring, and stage 3 — semantic similarity.

Weights are indicative defaults from docs/matching-engine.md; make them configurable
per user/search-profile rather than hardcoded once this grows real logic.

Skill-aware scoring (skills/transferable_skills/preferences) lives in
skill_matching.py's SkillMatcher, not here — it needs NormalizedJob.skills (LLM-
extracted at ingestion, see app/services/job_skill_extraction_service.py) and an
embedding call, so MatchingService computes it separately and merges it into
DeterministicScore before calling overall(). This class only covers the genuinely
synchronous, registry-free components: role/experience/salary/location.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

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
    ) -> tuple[float, float, float, float]:
        """Returns (role, experience, salary, location) — MatchingService merges
        these with SkillMatcher's output to build the full DeterministicScore."""
        return (
            self._role_score(job, preferences),
            self._experience_score(job, profile),
            self._salary_score(job, preferences),
            self._location_score(job, preferences),
        )

    def overall(self, deterministic: DeterministicScore, semantic_fit: float) -> float:
        """Weighted combination of every component, including the separately-computed
        semantic_fit. Result is 0-100."""
        w = self._weights
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

    def _role_score(self, job: NormalizedJob, preferences: UserPreference) -> float:
        if not preferences.preferred_roles:
            return 100.0
        title = job.title.lower()
        best = max(
            SequenceMatcher(None, title, role.replace("_", " ").lower()).ratio()
            for role in preferences.preferred_roles
        )
        return best * 100

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
