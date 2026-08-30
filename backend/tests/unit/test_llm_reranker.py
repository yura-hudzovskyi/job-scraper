import pytest

from app.domain.candidates.models import CandidateProfile, CandidateSkill, SkillLevel
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob
from app.domain.matching.llm_reranker import LlmReranker
from app.domain.matching.models import MatchGap, MatchReason, Recommendation, ScoreBreakdown
from app.integrations.ai.llm.base import LLMResult


class _FakeLlmProvider:
    def __init__(self, payload: dict):
        self._payload = payload
        self.call_count = 0

    async def structured_completion(self, prompt, schema):
        self.call_count += 1
        assert "Backend Engineer" in prompt
        assert "NestJS" in prompt  # a gap label passed in
        return LLMResult(data=schema(**self._payload), model_label="fake-model")


class _FakeBudget:
    def __init__(self, allow: bool):
        self._allow = allow
        self.calls = 0

    async def try_consume(self) -> bool:
        self.calls += 1
        return self._allow


def _job() -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title="Backend Engineer",
        company="Acme",
        description="We use Django and PostgreSQL.",
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        salary=None,
        seniority=None,
        required_experience_years=None,
    )


def _profile() -> CandidateProfile:
    return CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=5.0,
        roles=["Backend Developer"],
        skills=[CandidateSkill(name="Django", level=SkillLevel.COMMERCIAL)],
    )


_BREAKDOWN = ScoreBreakdown(
    skills=90, role=90, experience=90, semantic_fit=90, salary=100, location=100,
    transferable_skills=90, preferences=100,
)
_STRENGTHS = [MatchReason(label="Django", detail="Django appears in the job and in your profile")]
_GAPS = [MatchGap(label="NestJS", critical=False)]

_VERDICT_PAYLOAD = {
    "overall_fit": 82.0,
    "recommendation": "apply",
    "confidence": 0.75,
    "strengths": ["Django"],
    "gaps": ["NestJS"],
    "critical_gaps": [],
    "transferable_experience": ["NestJS via Django"],
    "interview_risk": "low",
    "summary": "Strong backend fit.",
    "recommended_cv": "backend",
}


@pytest.mark.asyncio
async def test_assess_maps_llm_verdict_to_llm_assessment() -> None:
    provider = _FakeLlmProvider(_VERDICT_PAYLOAD)
    reranker = LlmReranker(provider, _FakeBudget(allow=True))  # type: ignore[arg-type]

    assessment = await reranker.assess(_job(), _profile(), _BREAKDOWN, _STRENGTHS, _GAPS)

    assert assessment is not None
    assert assessment.overall_fit == 82.0
    assert assessment.recommendation == Recommendation.APPLY
    assert assessment.confidence == 0.75
    assert assessment.strengths == ["Django"]
    assert assessment.gaps == ["NestJS"]
    assert assessment.transferable_experience == ["NestJS via Django"]
    assert assessment.interview_risk == "low"
    assert assessment.summary == "Strong backend fit."
    assert assessment.recommended_cv == "backend"
    assert assessment.model_label == "fake-model"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_assess_returns_none_without_calling_the_llm_when_budget_exhausted() -> None:
    provider = _FakeLlmProvider(_VERDICT_PAYLOAD)
    reranker = LlmReranker(provider, _FakeBudget(allow=False))  # type: ignore[arg-type]

    assessment = await reranker.assess(_job(), _profile(), _BREAKDOWN, _STRENGTHS, _GAPS)

    assert assessment is None
    assert provider.call_count == 0
