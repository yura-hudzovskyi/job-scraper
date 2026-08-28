import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.candidates.skill_data import build_default_skill_registry
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob
from app.domain.matching.filters import HardFilterService
from app.domain.matching.models import Recommendation
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService


class _IdenticalVectorsProvider:
    """Cosine similarity always 1.0 — isolates a test from semantic_fit."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _OrthogonalVectorsProvider:
    """Cosine similarity always 0.0 — simulates "no semantic relation" for a test
    that specifically wants a weak/unrelated match, independent of skill overlap."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0], [0.0, 1.0]][: len(texts)]


def _job(
    title: str = "Senior Backend Engineer",
    description: str = "We use Django and PostgreSQL.",
    company: str = "Acme",
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
    registry = build_default_skill_registry()
    return MatchingService(
        HardFilterService(),
        DeterministicScorer(registry),
        SemanticScorer(embedding_provider),  # type: ignore[arg-type]
    )


@pytest.fixture
def matching_service() -> MatchingService:
    return _matching_service(_IdenticalVectorsProvider())


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
    job = _job(description="We use Django and PostgreSQL.")
    profile = _profile(skills=["Django", "PostgreSQL"])

    match = await matching_service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.recommendation == Recommendation.APPLY
    assert match.practical_fit > 80
    assert any(reason.label == "django" for reason in match.strengths)


@pytest.mark.asyncio
async def test_practical_fit_exceeds_requirement_match_when_skills_are_only_transferable(
    matching_service: MatchingService,
) -> None:
    job = _job(title="Backend Engineer", description="We use FastAPI.")
    profile = _profile(skills=["Django"])  # no FastAPI, but transfers via Django

    match = await matching_service.evaluate("canonical-1", job, profile, _preferences())

    assert match.practical_fit > match.requirement_match
    assert any(gap.label == "fastapi" for gap in match.gaps)


@pytest.mark.asyncio
async def test_weak_match_recommends_skip() -> None:
    service = _matching_service(_OrthogonalVectorsProvider())
    job = _job(title="Backend Engineer", description="We use Rust and Kafka.")
    profile = _profile(skills=["Photoshop"])

    match = await service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.recommendation == Recommendation.SKIP
