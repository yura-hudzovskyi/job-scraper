import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.candidates.skill_data import build_default_skill_registry
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, SalaryRange
from app.domain.matching.scoring import DeterministicScorer, ScoringWeights, SemanticScorer


def _job(
    title: str = "Senior Python Developer",
    description: str = "We use Django and PostgreSQL, deployed with Docker.",
    remote: bool = True,
    countries: list[str] | None = None,
    salary: SalaryRange | None = None,
    required_experience_years: float | None = None,
) -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title=title,
        company="Acme",
        description=description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=remote, countries=countries or []),
        salary=salary,
        seniority=None,
        required_experience_years=required_experience_years,
    )


def _profile(
    experience_years: float = 3.0,
    roles: list[str] | None = None,
    skills: list[str] | None = None,
) -> CandidateProfile:
    return CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=experience_years,
        roles=roles or [],
        skills=[CandidateSkill(name=name, level=SkillLevel.COMMERCIAL) for name in (skills or [])],
    )


def _preferences(**overrides) -> UserPreference:
    defaults = {"user_id": "u1", "desired_salary_usd": None}
    defaults.update(overrides)
    return UserPreference(**defaults)


@pytest.fixture
def scorer() -> DeterministicScorer:
    return DeterministicScorer(build_default_skill_registry())


def test_exact_skill_match_scores_full_marks(scorer: DeterministicScorer) -> None:
    job = _job(title="Backend Engineer", description="We use Django and PostgreSQL.")
    profile = _profile(skills=["Django", "PostgreSQL"])
    result = scorer.score(job, profile, _preferences())
    assert result.skills == 100.0
    assert result.transferable_skills == 100.0


def test_related_skill_scores_via_transferability_not_zero(scorer: DeterministicScorer) -> None:
    job = _job(title="Backend Engineer", description="We use FastAPI for our backend.")
    profile = _profile(skills=["Django"])  # no FastAPI, but Django transfers
    result = scorer.score(job, profile, _preferences())
    assert result.skills == 0.0  # no exact match
    assert result.transferable_skills == pytest.approx(70.0)  # django->fastapi = 0.7


def test_missing_skill_with_no_related_experience_scores_zero_transferable(
    scorer: DeterministicScorer,
) -> None:
    job = _job(description="We use Rust for systems programming.")
    profile = _profile(skills=["Photoshop"])  # unrelated, and not in the registry anyway
    result = scorer.score(job, profile, _preferences())
    assert result.skills == 0.0
    assert result.transferable_skills == 0.0


def test_no_mentioned_skills_defaults_to_full_marks(scorer: DeterministicScorer) -> None:
    job = _job(title="Generalist", description="Great team, flexible hours.")
    profile = _profile(skills=[])
    result = scorer.score(job, profile, _preferences())
    assert result.skills == 100.0
    assert result.transferable_skills == 100.0


def test_role_score_rewards_close_title_match(scorer: DeterministicScorer) -> None:
    job = _job(title="Senior Full Stack Engineer")
    exact = scorer.score(job, _profile(), _preferences(preferred_roles=["full_stack"]))
    unrelated = scorer.score(job, _profile(), _preferences(preferred_roles=["data_scientist"]))
    assert exact.role > unrelated.role


def test_role_score_defaults_to_full_marks_with_no_preference(
    scorer: DeterministicScorer,
) -> None:
    result = scorer.score(_job(), _profile(), _preferences())
    assert result.role == 100.0


def test_experience_score_full_marks_when_candidate_meets_requirement(
    scorer: DeterministicScorer,
) -> None:
    job = _job(required_experience_years=3.0)
    result = scorer.score(job, _profile(experience_years=5.0), _preferences())
    assert result.experience == 100.0


def test_experience_score_scales_down_when_candidate_falls_short(
    scorer: DeterministicScorer,
) -> None:
    job = _job(required_experience_years=6.0)
    result = scorer.score(job, _profile(experience_years=3.0), _preferences())
    assert result.experience == pytest.approx(50.0)


def test_salary_score_full_marks_when_job_meets_desired(scorer: DeterministicScorer) -> None:
    job = _job(salary=SalaryRange(min=4000, max=6000, currency="USD"))
    result = scorer.score(job, _profile(), _preferences(desired_salary_usd=4000))
    assert result.salary == 100.0


def test_salary_score_scales_down_when_job_pays_less(scorer: DeterministicScorer) -> None:
    job = _job(salary=SalaryRange(min=1500, max=2000, currency="USD"))
    result = scorer.score(job, _profile(), _preferences(desired_salary_usd=4000))
    assert result.salary == pytest.approx(50.0)


def test_salary_score_neutral_for_non_usd_currency(scorer: DeterministicScorer) -> None:
    job = _job(salary=SalaryRange(min=50000, max=60000, currency="UAH"))
    result = scorer.score(job, _profile(), _preferences(desired_salary_usd=4000))
    assert result.salary == 100.0


def test_location_score_full_marks_for_open_remote(scorer: DeterministicScorer) -> None:
    job = _job(remote=True, countries=[])
    result = scorer.score(job, _profile(), _preferences(locations=["Ukraine"]))
    assert result.location == 100.0


def test_location_score_penalizes_no_overlap(scorer: DeterministicScorer) -> None:
    job = _job(remote=True, countries=["United States"])
    result = scorer.score(job, _profile(), _preferences(locations=["Ukraine"]))
    assert result.location == 50.0


def test_preferences_score_rewards_preferred_stack(scorer: DeterministicScorer) -> None:
    job = _job(title="Frontend Engineer", description="Built with React and TypeScript.")
    result = scorer.score(
        job, _profile(), _preferences(preferred_stack=["React", "TypeScript"])
    )
    assert result.preferences == 100.0


def test_preferences_score_penalizes_stack_outside_preferred_or_acceptable(
    scorer: DeterministicScorer,
) -> None:
    job = _job(description="Built with PHP and jQuery era stack.")
    result = scorer.score(
        job, _profile(), _preferences(preferred_stack=["React"], acceptable_stack=["Vue"])
    )
    assert result.preferences == 0.0


def test_overall_combines_components_by_weight() -> None:
    weights = ScoringWeights(
        skills=1.0,
        role=0.0,
        semantic_fit=0.0,
        experience=0.0,
        transferable_skills=0.0,
        salary=0.0,
        location=0.0,
        preferences=0.0,
    )
    scorer = DeterministicScorer(build_default_skill_registry(), weights)
    job = _job(title="Backend Engineer", description="We use Django.")
    deterministic = scorer.score(job, _profile(skills=["Django"]), _preferences())
    assert scorer.overall(deterministic, semantic_fit=0.0) == pytest.approx(100.0)


class _FakeEmbeddingProvider:
    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


@pytest.mark.asyncio
async def test_semantic_scorer_returns_one_for_identical_vectors() -> None:
    profile_text = "roles skills experience"
    job = _job(description="We use Django and PostgreSQL.")
    job_text = f"{job.title}\n{job.description}"
    provider = _FakeEmbeddingProvider({profile_text: [1.0, 0.0], job_text: [1.0, 0.0]})
    profile = CandidateProfile(
        id="p1", user_id="u1", experience_years=1, roles=["roles", "skills", "experience"], skills=[]
    )

    scorer = SemanticScorer(provider)  # type: ignore[arg-type]
    similarity = await scorer.similarity(job, profile)
    assert similarity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_semantic_scorer_returns_zero_for_orthogonal_vectors() -> None:
    profile_text = "profile"
    job = _job(description="We use Django and PostgreSQL.")
    job_text = f"{job.title}\n{job.description}"
    provider = _FakeEmbeddingProvider({profile_text: [1.0, 0.0], job_text: [0.0, 1.0]})
    profile = CandidateProfile(id="p1", user_id="u1", experience_years=1, roles=["profile"], skills=[])

    scorer = SemanticScorer(provider)  # type: ignore[arg-type]
    similarity = await scorer.similarity(job, profile)
    assert similarity == pytest.approx(0.0)
