import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    NormalizedJobSkill,
    RequirementType,
)
from app.domain.matching.filters import HardFilterService
from app.domain.matching.hybrid import SCORER_VERSION, HybridMatchEngine
from app.domain.matching.models import (
    JobMatch,
    LlmAssessment,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.matching.provenance import (
    AnalysisLevel,
    FallbackReason,
    MatchEngine,
    MatchProvenance,
    PipelineModels,
)
from app.domain.matching.role_matching import RoleMatcher
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.service import MatchingService
from app.domain.matching.skill_matching import SkillMatcher
from app.integrations.ai.routing.router import Capability, NoCapacity


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
        models=PipelineModels(embedding="all-MiniLM-L6-v2"),
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
        provenance=MatchProvenance(
            engine=MatchEngine.DETERMINISTIC, analysis_level=AnalysisLevel.STANDARD
        ),
    )


class _FakeLlmReranker:
    def __init__(self, assessment: LlmAssessment | None = None, error: Exception | None = None):
        self._assessment = assessment
        self._error = error
        self.call_count = 0

    async def assess(self, job, profile, breakdown, strengths, gaps) -> LlmAssessment:
        self.call_count += 1
        if self._error is not None:
            raise self._error
        assert self._assessment is not None
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

    assert result.llm_assessment is None
    assert result.provenance is not None
    assert result.provenance.fallback_reason is FallbackReason.NO_LLM_PROVIDER
    # Only the provenance is amended — the score itself is untouched.
    assert result.practical_fit == match.practical_fit


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

    assert result.llm_assessment is None
    assert reranker.call_count == 0
    assert result.provenance is not None
    assert result.provenance.fallback_reason is FallbackReason.BELOW_LLM_THRESHOLD


@pytest.mark.asyncio
async def test_should_i_apply_is_a_noop_when_reranker_returns_none() -> None:
    # Budget exhausted (or any other degrade-gracefully reason) -> reranker
    # itself returns None rather than raising; should_i_apply must leave the
    # match untouched, not error.
    reranker = _FakeLlmReranker(error=NoCapacity(Capability.MATCH_ENRICHMENT))
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=reranker)
    match = _match(Recommendation.APPLY)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert reranker.call_count == 1
    assert result.llm_assessment is None
    assert result.provenance is not None
    assert result.provenance.fallback_reason is FallbackReason.LLM_NO_CAPACITY


@pytest.mark.asyncio
async def test_should_i_apply_populates_llm_assessment_when_apply_and_budget_allows() -> None:
    reranker = _FakeLlmReranker(_FAKE_ASSESSMENT)
    service = _matching_service(_FakeEmbeddingProvider(), llm_reranker=reranker)
    match = _match(Recommendation.APPLY)

    result = await service.should_i_apply(_job(), _profile(), match)

    assert result.llm_assessment == _FAKE_ASSESSMENT
    assert reranker.call_count == 1
    assert result.provenance is not None
    # An LLM verdict on top is what makes the analysis "full", and the model that
    # produced it is recorded on the result rather than looked up later.
    assert result.provenance.analysis_level is AnalysisLevel.FULL
    assert result.provenance.match_model == _FAKE_ASSESSMENT.model_label
    assert result.provenance.fallback_reason is None


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
            NormalizedJobSkill(name="Django", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="PostgreSQL", requirement=RequirementType.REQUIRED_EXPLICIT),
        ],
        skills_extracted_by="Groq (llama-3.3-70b-versatile)",
    )
    profile = _profile(skills=["Django", "PostgreSQL"])

    match = await matching_service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.recommendation == Recommendation.APPLY
    assert match.practical_fit > 80
    assert match.provenance is not None
    assert match.provenance.skills_model == "Groq (llama-3.3-70b-versatile)"
    assert match.provenance.embedding_model == "all-MiniLM-L6-v2"
    assert match.provenance.analysis_level is AnalysisLevel.STANDARD
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
        skills=[NormalizedJobSkill(name="FastAPI", requirement=RequirementType.REQUIRED_EXPLICIT)],
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
        skills=[NormalizedJobSkill(name="Excel", requirement=RequirementType.REQUIRED_EXPLICIT)],
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
            NormalizedJobSkill(name="Rust", requirement=RequirementType.REQUIRED_EXPLICIT),
            NormalizedJobSkill(name="Kafka", requirement=RequirementType.REQUIRED_EXPLICIT),
        ],
    )
    profile = _profile(skills=["Photoshop"])

    match = await service.evaluate("canonical-1", job, profile, _preferences())

    assert match.eligible is True
    assert match.recommendation == Recommendation.SKIP


def _hybrid_service(embedding_provider: object) -> MatchingService:
    return MatchingService(
        HardFilterService(),
        DeterministicScorer(),
        SemanticScorer(embedding_provider),  # type: ignore[arg-type]
        SkillMatcher(embedding_provider),  # type: ignore[arg-type]
        RoleMatcher(embedding_provider),  # type: ignore[arg-type]
        models=PipelineModels(embedding="all-MiniLM-L6-v2"),
        hybrid_engine=HybridMatchEngine(),
    )


@pytest.mark.asyncio
async def test_the_hybrid_engine_owns_the_score_when_it_is_enabled() -> None:
    service = _hybrid_service(_FakeEmbeddingProvider())
    job = _job(
        skills=[NormalizedJobSkill(name="Django", requirement=RequirementType.REQUIRED_EXPLICIT)],
        skills_extracted_by="Groq (llama-3.3-70b-versatile)",
    )

    match = await service.evaluate("canonical-1", job, _profile(skills=["Django"]), _preferences())

    assert match.provenance is not None
    assert match.provenance.engine is MatchEngine.HYBRID
    # The score now says how sure it is, separately from how high it is.
    assert match.confidence is not None and 0.0 < match.confidence <= 1.0
    # Pinning the constant rather than a literal: the point is that the scorer
    # version is recorded, and it moves whenever the rules change.
    assert match.provenance.versions.scorer == SCORER_VERSION
    assert match.requirement_match == 100.0


@pytest.mark.asyncio
async def test_the_hybrid_engine_reports_unknowns_as_risks_not_gaps() -> None:
    # Explicit vectors: this fake makes every unlisted text identical, so an
    # unrelated skill would otherwise "match" the candidate's at similarity 1.0.
    service = _hybrid_service(
        _FakeEmbeddingProvider({"Rust": [0.0, 1.0], "Django": [1.0, 0.0]})
    )
    job = _job(
        skills=[NormalizedJobSkill(name="Rust", requirement=RequirementType.UNKNOWN)],
        skills_extracted_by="Groq (llama-3.3-70b-versatile)",
    )

    match = await service.evaluate("canonical-1", job, _profile(skills=["Django"]), _preferences())

    assert match.gaps == []
    assert any("without saying whether they are required" in risk for risk in match.risks)


@pytest.mark.asyncio
async def test_without_the_flag_nothing_about_the_old_path_changes() -> None:
    # The pre-v3 pipeline stays the default until the flag is switched on.
    service = _matching_service(_FakeEmbeddingProvider())
    job = _job(skills=[NormalizedJobSkill(name="Django", requirement=RequirementType.REQUIRED_EXPLICIT)])

    match = await service.evaluate("canonical-1", job, _profile(skills=["Django"]), _preferences())

    assert match.provenance is not None
    assert match.provenance.engine is MatchEngine.DETERMINISTIC
    assert match.confidence is None
    assert match.risks == []
