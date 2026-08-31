import uuid

import pytest

from app.domain.candidates.models import CandidateProfile, CandidateSkill, SkillLevel
from app.integrations.ai.llm.base import LLMResult
from app.services.profile_service import LlmNotConfigured, ProfileService


class _FakeLlmProvider:
    def __init__(self, payload: dict):
        self._payload = payload

    async def structured_completion(self, prompt, schema):
        assert "Backend Developer" in prompt
        return LLMResult(data=schema(**self._payload), model_label="fake-model")


class _FakeCandidateRepository:
    def __init__(self, profile: CandidateProfile | None):
        self._profile = profile

    async def get_latest_candidate_profile(self, user_id: uuid.UUID) -> CandidateProfile | None:
        return self._profile


def _profile() -> CandidateProfile:
    return CandidateProfile(
        id="p1",
        user_id="u1",
        experience_years=5.0,
        roles=["Backend Developer"],
        skills=[CandidateSkill(name="Django", level=SkillLevel.STRONG)],
    )


_SUGGESTION_PAYLOAD = {
    "desired_salary_usd": 4000,
    "preferred_roles": ["Backend Developer"],
    "preferred_stack": ["Django"],
    "acceptable_stack": [],
    "work_formats": [],
    "locations": [],
    "max_required_experience": 6.0,
}


@pytest.mark.asyncio
async def test_suggest_preferences_maps_llm_output_to_a_user_preference() -> None:
    repository = _FakeCandidateRepository(_profile())
    service = ProfileService(repository, llm_provider=_FakeLlmProvider(_SUGGESTION_PAYLOAD))  # type: ignore[arg-type]
    user_id = uuid.uuid4()

    suggestion = await service.suggest_preferences(user_id)

    assert suggestion.preferences.user_id == str(user_id)
    assert suggestion.preferences.desired_salary_usd == 4000
    assert suggestion.preferences.preferred_stack == ["Django"]
    assert suggestion.model_label == "fake-model"


@pytest.mark.asyncio
async def test_suggest_preferences_without_llm_provider_raises_clear_error() -> None:
    service = ProfileService(_FakeCandidateRepository(_profile()), llm_provider=None)  # type: ignore[arg-type]

    with pytest.raises(LlmNotConfigured):
        await service.suggest_preferences(uuid.uuid4())


@pytest.mark.asyncio
async def test_suggest_preferences_raises_when_no_profile_analyzed_yet() -> None:
    service = ProfileService(
        _FakeCandidateRepository(None), llm_provider=_FakeLlmProvider(_SUGGESTION_PAYLOAD)
    )  # type: ignore[arg-type]

    with pytest.raises(LookupError):
        await service.suggest_preferences(uuid.uuid4())
