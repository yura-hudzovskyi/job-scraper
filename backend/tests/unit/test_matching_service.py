import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, NormalizedJobSkill
from app.domain.matching.filters import HardFilterService
from app.domain.matching.models import (
    JobMatch,
    LlmAssessment,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.matching.role_matching import RoleMatcher
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


def _matching_service(
    embedding_provider: object,
    llm_reranker: object | None = None,
) -> MatchingService:
    return MatchingService(
        HardFilterService(),
        DeterministicScorer(),
        SemanticScorer(embedding_provider),  # type: ignore[arg-type]
        SkillMatcher(embedding_provider),  # type: ignore[arg-type]
        RoleMatcher(embedding_provider),  # type: ignore[arg-type]
        llm_reranker=llm_reranker,  # type: ignore[arg-type]
    )


def _match(recommendation: Recommendation) -> JobMatch:
    return JobMatch(
        id="m1",
        user_id="u1",
        canonical_job_id="c1",
        eligible=True,
        requirement_match=80.0,
        practical_fit=80.0,
        breakdown=ScoreBreakdown(90, 90, 90, 90, 100, 100, 90, 100),
        recommendation=recommendation,
    )


class _FakeLlmReranker:
    def __init__(self, assessment: LlmAssessment | None):
        self._assessment = assessment
        self.call_count = 0

    async def assess(self, job, profile, breakdown, strengths, gaps) -> LlmAssessment | None:
        self.call_count += 1
        return self._assessment


_FAKE_ASSESSMENT = LlmAssessment(
    overall_fit=85.0,
    recommendation=Recommendation.APPLY,
    confidence=0.8,
    strengths=["Django"],
    gaps=[],
    critical_gaps=[],
    transferable_experience=[],
    interview_risk="low",
    summary="Good fit.",
    recommended_cv=None,
    model_label="fake-model",
)


@pytest.mark.asyncio
async def test_should_i_apply_is_a_noop_without_a_reranker() -> None:
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=None)
    match = _match(Recommendation.APPLY)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert result is match
    assert result.llm_assessment is None


@pytest.mark.asyncio
async def test_should_i_apply_runs_for_consider_recommendation_too() -> None:
    # Widened gate: the LLM overlay isn't APPLY-only anymore — CONSIDER-tier matches
    # (a decent match per the deterministic pipeline) are worth a closer look too.
    reranker = _FakeLlmReranker(_FAKE_ASSESSMENT)
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=reranker)
    match = _match(Recommendation.CONSIDER)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert result.llm_assessment == _FAKE_ASSESSMENT
    assert reranker.call_count == 1


@pytest.mark.asyncio
async def test_should_i_apply_is_a_noop_for_skip_recommendation() -> None:
    reranker = _FakeLlmReranker(_FAKE_ASSESSMENT)
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=reranker)
    match = _match(Recommendation.SKIP)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert result is match
    assert reranker.call_count == 0


@pytest.mark.asyncio
async def test_should_i_apply_is_a_noop_when_reranker_returns_none() -> None:
    # Budget exhausted (or any other degrade-gracefully reason) -> reranker
    # itself returns None rather than raising; should_i_apply must leave the
    # match untouched, not error.
    reranker = _FakeLlmReranker(None)
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=reranker)
    match = _match(Recommendation.APPLY)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert result is match
    assert reranker.call_count == 1
    assert result.llm_assessment is None


@pytest.mark.asyncio
async def test_should_i_apply_populates_llm_assessment_when_apply_and_budget_allows() -> None:
    reranker = _FakeLlmReranker(_FAKE_ASSESSMENT)
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=reranker)
    match = _match(Recommendation.APPLY)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert result.llm_assessment == _FAKE_ASSESSMENT
    assert reranker.call_count == 1


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
async def test_unrelated_job_with_no_extracted_skills_is_not_a_false_positive() -> None:
    # Regression test: an "Account Manager" posting has no technical skills to
    # extract at all, so job.skills stays empty. That must not fall back to a
    # fabricated "perfect" skills/transferable/preferences score against a developer
    # profile — role and semantic mismatch should drag the overall score down to SKIP.
    provider = _FakeEmbeddingProvider(
        {
            "Backend Developer\nPython": [1.0, 0.0],
            "Account Manager\nManage client relationships and sales pipeline.": [0.0, 1.0],
            # RoleMatcher embeds the job title and CV-derived roles standalone
            # (separately from SemanticScorer's full-text embed above) — without
            # these, both would fall back to the same default vector and role
            # would wrongly read as a perfect 100 instead of a mismatch.
            "Account Manager": [0.0, 1.0],
            "Backend Developer": [1.0, 0.0],
        }
    )
    service = _matching_service(provider)
    job = _job(
        title="Account Manager",
        description="Manage client relationships and sales pipeline.",
        skills=[],
    )
    profile = CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=5.0,
        roles=["Backend Developer"],
        skills=[CandidateSkill(name="Python", level=SkillLevel.COMMERCIAL)],
    )

    match = await service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.practical_fit < 55.0
    assert match.recommendation == Recommendation.SKIP


@pytest.mark.asyncio
async def test_domain_mismatch_gate_forces_skip_despite_superficial_skill_match() -> None:
    # A job that skill-matches superficially (skills_available=True — Excel is a
    # real, matched skill) but is a different profession entirely — role and
    # semantic both say so — floors at ~70 (CONSIDER band) on the score alone,
    # since skills/transferable/experience/salary/location all read as neutral or
    # perfect. The domain-mismatch gate overrides the recommendation to SKIP
    # without altering the score itself.
    provider = _FakeEmbeddingProvider(
        {
            "Excel": [1.0, 0.0],
            "Account Manager": [0.0, 1.0],
            "Backend Developer": [1.0, 0.0],
            "Backend Developer\nExcel": [1.0, 0.0],
            "Account Manager\nManage client relationships.": [0.0, 1.0],
        }
    )
    service = _matching_service(provider)
    job = _job(
        title="Account Manager",
        description="Manage client relationships.",
        skills=[NormalizedJobSkill(name="Excel", required=True)],
    )
    profile = CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=5.0,
        roles=["Backend Developer"],
        skills=[CandidateSkill(name="Excel", level=SkillLevel.COMMERCIAL)],
    )

    match = await service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.practical_fit == pytest.approx(70.0)
    assert match.recommendation == Recommendation.SKIP
    assert any(gap.label == "role/domain mismatch" for gap in match.gaps)


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
