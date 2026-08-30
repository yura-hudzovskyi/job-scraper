import pytest

from app.domain.candidates.models import CandidateProfile, UserPreference
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, SalaryRange
from app.domain.matching.scoring import (
    DeterministicScore,
    DeterministicScorer,
    ScoringWeights,
    SemanticScorer,
)


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


def _profile(experience_years: float = 3.0) -> CandidateProfile:
    return CandidateProfile(id="p1", user_id="u1", experience_years=experience_years, roles=[], skills=[])


def _preferences(**overrides) -> UserPreference:
    defaults = {"user_id": "u1", "desired_salary_usd": None}
    defaults.update(overrides)
    return UserPreference(**defaults)


@pytest.fixture
def scorer() -> DeterministicScorer:
    return DeterministicScorer()


def test_experience_score_full_marks_when_candidate_meets_requirement(
    scorer: DeterministicScorer,
) -> None:
    job = _job(required_experience_years=3.0)
    experience, _salary, _location = scorer.score(job, _profile(experience_years=5.0), _preferences())
    assert experience == 100.0


def test_experience_score_scales_down_when_candidate_falls_short(
    scorer: DeterministicScorer,
) -> None:
    job = _job(required_experience_years=6.0)
    experience, _salary, _location = scorer.score(job, _profile(experience_years=3.0), _preferences())
    assert experience == pytest.approx(50.0)


def test_salary_score_full_marks_when_job_meets_desired(scorer: DeterministicScorer) -> None:
    job = _job(salary=SalaryRange(min=4000, max=6000, currency="USD"))
    _experience, salary, _location = scorer.score(job, _profile(), _preferences(desired_salary_usd=4000))
    assert salary == 100.0


def test_salary_score_scales_down_when_job_pays_less(scorer: DeterministicScorer) -> None:
    job = _job(salary=SalaryRange(min=1500, max=2000, currency="USD"))
    _experience, salary, _location = scorer.score(job, _profile(), _preferences(desired_salary_usd=4000))
    assert salary == pytest.approx(50.0)


def test_salary_score_neutral_for_non_usd_currency(scorer: DeterministicScorer) -> None:
    job = _job(salary=SalaryRange(min=50000, max=60000, currency="UAH"))
    _experience, salary, _location = scorer.score(job, _profile(), _preferences(desired_salary_usd=4000))
    assert salary == 100.0


def test_location_score_full_marks_for_open_remote(scorer: DeterministicScorer) -> None:
    job = _job(remote=True, countries=[])
    _experience, _salary, location = scorer.score(job, _profile(), _preferences(locations=["Ukraine"]))
    assert location == 100.0


def test_location_score_penalizes_no_overlap(scorer: DeterministicScorer) -> None:
    job = _job(remote=True, countries=["United States"])
    _experience, _salary, location = scorer.score(job, _profile(), _preferences(locations=["Ukraine"]))
    assert location == 50.0


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
    scorer = DeterministicScorer(weights)
    deterministic = DeterministicScore(
        skills=100.0, role=0.0, experience=0.0, transferable_skills=0.0, salary=0.0, location=0.0, preferences=0.0
    )
    assert scorer.overall(deterministic, semantic_fit=0.0) == pytest.approx(100.0)


def test_overall_redistributes_skill_weight_when_job_has_no_extracted_skills() -> None:
    # A job with no extracted skills makes SkillMatcher report a fabricated neutral
    # 100 for skills/transferable/preferences (nothing required -> nothing missing).
    # skills_available=False must stop that from being credited at full weight.
    scorer = DeterministicScorer()
    deterministic = DeterministicScore(
        skills=100.0,
        role=0.0,
        experience=100.0,
        transferable_skills=100.0,
        salary=100.0,
        location=100.0,
        preferences=100.0,
    )
    with_skills = scorer.overall(deterministic, semantic_fit=0.0, skills_available=True)
    without_skills = scorer.overall(deterministic, semantic_fit=0.0, skills_available=False)
    assert without_skills < with_skills


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
