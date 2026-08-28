"""Stage 2 — deterministic weighted scoring, and stage 3 — semantic similarity.

Weights are indicative defaults from docs/matching-engine.md; make them configurable
per user/search-profile rather than hardcoded once this grows real logic.

NormalizedJob has no structured skill list yet (that's Phase 4's LLM requirement
extraction) — every skill-aware component here works off SkillRegistry.extract_mentions
over the job's title+description instead. It's an approximation, not a stand-in for
real requirement extraction, but it's honest, deterministic, and immediately useful.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.candidates.skills import SkillRegistry
from app.domain.jobs.models import NormalizedJob
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
    """Everything ScoreBreakdown needs except semantic_fit, which needs embeddings
    and is computed separately by SemanticScorer. See MatchingService."""

    skills: float
    role: float
    experience: float
    transferable_skills: float
    salary: float
    location: float
    preferences: float


class DeterministicScorer:
    def __init__(self, skill_registry: SkillRegistry, weights: ScoringWeights | None = None):
        self._skill_registry = skill_registry
        self._weights = weights or ScoringWeights()

    def score(
        self,
        job: NormalizedJob,
        profile: CandidateProfile,
        preferences: UserPreference,
    ) -> DeterministicScore:
        job_text = f"{job.title}\n{job.description}"
        mentioned_skills = self._skill_registry.extract_mentions(job_text)
        candidate_skills = {
            resolved
            for skill in profile.skills
            if (resolved := self._skill_registry.resolve(skill.name)) is not None
        }

        skills_score, transferable_score = self._skill_scores(mentioned_skills, candidate_skills)

        return DeterministicScore(
            skills=skills_score,
            transferable_skills=transferable_score,
            role=self._role_score(job, preferences),
            experience=self._experience_score(job, profile),
            salary=self._salary_score(job, preferences),
            location=self._location_score(job, preferences),
            preferences=self._preferences_score(mentioned_skills, preferences),
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

    def skill_gap_analysis(
        self, job: NormalizedJob, profile: CandidateProfile
    ) -> tuple[list[str], list[str]]:
        """(exact_matches, missing) canonical skill names, text-mined from the job —
        for building human-readable strengths/gaps, not scoring itself."""
        mentioned_skills = self._skill_registry.extract_mentions(f"{job.title}\n{job.description}")
        candidate_skills = {
            resolved
            for skill in profile.skills
            if (resolved := self._skill_registry.resolve(skill.name)) is not None
        }
        exact = [skill for skill in mentioned_skills if skill in candidate_skills]
        missing = [skill for skill in mentioned_skills if skill not in candidate_skills]
        return exact, missing

    def _skill_scores(
        self, mentioned_skills: list[str], candidate_skills: set[str]
    ) -> tuple[float, float]:
        if not mentioned_skills:
            return 100.0, 100.0

        exact = [skill for skill in mentioned_skills if skill in candidate_skills]
        missing = [skill for skill in mentioned_skills if skill not in candidate_skills]
        skills_score = len(exact) / len(mentioned_skills) * 100

        if not missing:
            return skills_score, 100.0

        transfer_values = [
            max(
                (self._skill_registry.transferability(cand, gap) for cand in candidate_skills),
                default=0.0,
            )
            for gap in missing
        ]
        transferable_score = sum(transfer_values) / len(missing) * 100
        return skills_score, transferable_score

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

    def _preferences_score(
        self, mentioned_skills: list[str], preferences: UserPreference
    ) -> float:
        """How much of the job's (text-mined) stack is something the candidate said
        they want or would accept."""
        if not mentioned_skills or (not preferences.preferred_stack and not preferences.acceptable_stack):
            return 100.0

        preferred = {self._skill_registry.resolve(s) or s.lower() for s in preferences.preferred_stack}
        acceptable = {
            self._skill_registry.resolve(s) or s.lower() for s in preferences.acceptable_stack
        }

        weights = [
            1.0 if skill in preferred else 0.6 if skill in acceptable else 0.0
            for skill in mentioned_skills
        ]
        return sum(weights) / len(weights) * 100


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
        return _cosine_similarity(profile_vector, job_vector)


def _profile_text(profile: CandidateProfile) -> str:
    parts = [
        " ".join(profile.roles),
        " ".join(skill.name for skill in profile.skills),
        " ".join(f"{entry.title} {entry.description}" for entry in profile.experience),
        " ".join(profile.achievements),
        " ".join(profile.domains),
    ]
    return "\n".join(part for part in parts if part)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = float(sum(x * y for x, y in zip(a, b, strict=True)))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(max(0.0, min(1.0, dot / (norm_a * norm_b))))
