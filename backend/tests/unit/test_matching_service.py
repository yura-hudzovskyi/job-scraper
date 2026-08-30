import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, NormalizedJobSkill
from app.domain.matching.filters import HardFilterService
from app.domain.matching.models import Recommendation
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.domain.matching.skill_matching import SkillMatcher


class _FakeEmbeddingProvider:
    """Any text not explicitly overridden gets the same default vector, so unrelated
    calls (semantic_fit's profile/job text, skills not under test) trivially agree
    with each other (cosine 1.0) unless a test deliberately overrides them to differ.
    """

    def __init__(self, overrides: dict[str, list[float]] | None = None):
        self._overrides = overrides or {}
        self._default = [1.0, 0.0]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._overrides.get(text, self._default) for text in texts]


def _job(
    title: str = "Senior Backend Engineer",
    description: str = "We use Django and PostgreSQL.",
    company: str = "Acme",
    skills: list[NormalizedJobSkill] | None = None,
    skills_extracted_by: str | None = None,
) -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title=title,
        company=company,
        description=description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        salary=None,
        seniority=None,
        required_experience_years=None,
        skills=skills or [],
        skills_extracted_by=skills_extracted_by,
    )


def _profile(skills: list[str] | None = None) -> CandidateProfile:
    return CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=5.0,
        roles=[],
        skills=[CandidateSkill(name=name, level=SkillLevel.COMMERCIAL) for name in (skills or [])],
    )


def _preferences(**overrides) -> UserPreference:
    defaults = {"user_id": "u1", "desired_salary_usd": None}
    defaults.update(overrides)
    return UserPreference(**defaults)


def _matching_service(embedding_provider: object) -> MatchingService:
    return MatchingService(
        HardFilterService(),
        DeterministicScorer(),
        SemanticScorer(embedding_provider),  # type: ignore[arg-type]
        SkillMatcher(embedding_provider),  # type: ignore[arg-type]
    )


@pytest.fixture
def matching_service() -> MatchingService:
    return _matching_service(_FakeEmbeddingProvider())


@pytest.mark.asyncio
async def test_ineligible_job_short_circuits_before_scoring(
    matching_service: MatchingService,
) -> None:
    job = _job(company="Blacklisted Corp")
    preferences = _preferences(companies_blacklist=["Blacklisted Corp"])

    match = await matching_service.evaluate("canonical-1", job, _profile(), preferences)

    assert match.eligible is False
    assert match.requirement_match == 0.0
    assert match.practical_fit == 0.0
    assert match.recommendation == Recommendation.SKIP
    assert match.gaps and match.gaps[0].critical is True


@pytest.mark.asyncio
async def test_strong_match_recommends_apply(matching_service: MatchingService) -> None:
    job = _job(
        description="We use Django and PostgreSQL.",
        skills=[
            NormalizedJobSkill(name="Django", required=True),
            NormalizedJobSkill(name="PostgreSQL", required=True),
        ],
        skills_extracted_by="Ollama (llama3.2:3b)",
    )
    profile = _profile(skills=["Django", "PostgreSQL"])

    match = await matching_service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.recommendation == Recommendation.APPLY
    assert match.practical_fit > 80
    assert match.skills_source == "Ollama (llama3.2:3b)"
    assert any(reason.label == "Django" for reason in match.strengths)


@pytest.mark.asyncio
async def test_practical_fit_exceeds_requirement_match_when_skills_are_only_transferable() -> None:
    # cos(Django, FastAPI's default vector) = 0.5 — related enough for partial
    # transfer credit, below the match threshold so it's still a gap.
    provider = _FakeEmbeddingProvider({"Django": [0.5, 0.8660254]})
    service = _matching_service(provider)
    job = _job(
        title="Backend Engineer",
        description="We use FastAPI.",
        skills=[NormalizedJobSkill(name="FastAPI", required=True)],
    )
    profile = _profile(skills=["Django"])  # no FastAPI, but transfers via Django

    match = await service.evaluate("canonical-1", job, profile, _preferences())

    assert match.practical_fit > match.requirement_match
    assert any(gap.label == "FastAPI" for gap in match.gaps)


@pytest.mark.asyncio
async def test_weak_match_recommends_skip() -> None:
    provider = _FakeEmbeddingProvider(
        {
            "Rust": [0.0, 1.0],
            "Kafka": [0.0, 1.0],
            "Photoshop": [1.0, 0.0],
            "Backend Engineer\nWe use Rust and Kafka.": [0.0, 1.0],
        }
    )
    service = _matching_service(provider)
    job = _job(
        title="Backend Engineer",
        description="We use Rust and Kafka.",
        skills=[
            NormalizedJobSkill(name="Rust", required=True),
            NormalizedJobSkill(name="Kafka", required=True),
        ],
    )
    profile = _profile(skills=["Photoshop"])

    match = await service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.recommendation == Recommendation.SKIP
