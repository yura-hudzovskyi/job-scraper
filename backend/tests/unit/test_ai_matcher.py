import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob
from app.domain.matching.ai_matcher import AiMatcher
from app.domain.matching.models import Recommendation
from app.integrations.ai.llm.base import LLMResult


class _FakeLlmProvider:
    def __init__(self, payload: dict):
        self._payload = payload
        self.call_count = 0

    async def structured_completion(self, prompt, schema):
        self.call_count += 1
        assert "Backend Engineer" in prompt
        assert "Django" in prompt  # candidate skill passed in
        return LLMResult(data=schema(**self._payload), model_label="fake-model")


class _FailingLlmProvider:
    async def structured_completion(self, prompt, schema):
        raise RuntimeError("Ollama unreachable")


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


def _preferences() -> UserPreference:
    return UserPreference(user_id="u1", desired_salary_usd=None)


_VERDICT_PAYLOAD = {
    "requirement_match": 82.0,
    "practical_fit": 88.0,
    "breakdown": {
        "skills": 90,
        "role": 90,
        "experience": 90,
        "semantic_fit": 90,
        "salary": 100,
        "location": 100,
        "transferable_skills": 90,
        "preferences": 100,
    },
    "strengths": [{"label": "Django", "detail": "Both the job and candidate use Django"}],
    "gaps": [{"label": "PostgreSQL", "critical": True}],
    "recommendation": "apply",
}


@pytest.mark.asyncio
async def test_assess_maps_llm_verdict_into_a_job_match() -> None:
    provider = _FakeLlmProvider(_VERDICT_PAYLOAD)
    matcher = AiMatcher(provider)  # type: ignore[arg-type]

    match = await matcher.assess(_job(), _profile(), _preferences())

    assert match is not None
    assert match.eligible is True
    assert match.requirement_match == 82.0
    assert match.practical_fit == 88.0
    assert match.breakdown.skills == 90
    assert match.recommendation == Recommendation.APPLY
    assert match.strengths[0].label == "Django"
    assert match.gaps[0].critical is True
    assert match.scored_by == "AI (fake-model)"


@pytest.mark.asyncio
async def test_assess_returns_none_instead_of_raising_when_the_llm_call_fails() -> None:
    # MatchingService.evaluate falls back to the deterministic pipeline whenever
    # this returns None — a timeout, unreachable provider, or malformed output
    # must degrade gracefully, not crash score_job_for_user.
    matcher = AiMatcher(_FailingLlmProvider())  # type: ignore[arg-type]

    match = await matcher.assess(_job(), _profile(), _preferences())

    assert match is None
