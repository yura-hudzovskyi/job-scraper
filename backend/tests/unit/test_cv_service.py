import uuid

import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    SkillLevel,
    SkillOverride,
    SkillSource,
)
from app.integrations.ai.llm.base import LLMResult
from app.services.ai_errors import LlmCallFailed, LlmNotConfigured
from app.services.cv_service import CvService


class _FakeLlmProvider:
    def __init__(self, payload: dict):
        self._payload = payload

    async def structured_completion(self, prompt, schema):
        assert "CV" in prompt
        return LLMResult(data=schema(**self._payload), model_label="fake-model")


class _FakeCandidateRepository:
    def __init__(
        self,
        deletable_cv_ids: set[uuid.UUID] | None = None,
        skill_overrides: list[SkillOverride] | None = None,
    ) -> None:
        self.saved: tuple[uuid.UUID, uuid.UUID, CandidateProfile] | None = None
        self._deletable_cv_ids = deletable_cv_ids or set()
        self._skill_overrides = skill_overrides or []

    async def list_skill_overrides(self, user_id: uuid.UUID) -> list[SkillOverride]:
        return self._skill_overrides

    async def save_candidate_profile(
        self, user_id: uuid.UUID, cv_document_id: uuid.UUID, profile: CandidateProfile
    ) -> CandidateProfile:
        self.saved = (user_id, cv_document_id, profile)
        return profile

    async def delete_cv_document(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        return cv_document_id in self._deletable_cv_ids


_EXTRACTED_PAYLOAD = {
    "experience_years": 4.5,
    "roles": ["backend engineer", "full stack developer"],
    "skills": [
        {"name": "Python", "level": "strong", "years": 4.5},
        {"name": "Django", "level": "commercial", "years": 3.0},
    ],
    "experience": [
        {
            "company": "Acme",
            "title": "Backend Engineer",
            "start_date": "2022-01",
            "end_date": None,
            "description": "Built REST APIs.",
            "skills": ["Python", "Django"],
        }
    ],
    "achievements": ["Cut API latency by 40%"],
    "domains": ["fintech"],
    "ai_experience": [],
}


@pytest.mark.asyncio
async def test_analyze_cv_maps_llm_output_to_candidate_profile() -> None:
    repository = _FakeCandidateRepository()
    service = CvService(repository, llm_provider=_FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]
    user_id = uuid.uuid4()
    cv_document_id = uuid.uuid4()

    profile = await service.analyze_cv(user_id, cv_document_id, "some CV text")

    assert profile.experience_years == 4.5
    assert profile.roles == ["backend engineer", "full stack developer"]
    assert [skill.name for skill in profile.skills] == ["Python", "Django"]
    assert profile.skills[0].level.value == "strong"
    assert profile.experience[0].company == "Acme"
    assert profile.achievements == ["Cut API latency by 40%"]
    assert profile.generated_by == "fake-model"

    assert repository.saved is not None
    saved_user_id, saved_cv_document_id, _ = repository.saved
    assert saved_user_id == user_id
    assert saved_cv_document_id == cv_document_id


@pytest.mark.asyncio
async def test_analyze_cv_without_llm_provider_raises_clear_error() -> None:
    service = CvService(_FakeCandidateRepository(), llm_provider=None)  # type: ignore[arg-type]

    with pytest.raises(LlmNotConfigured):
        await service.analyze_cv(uuid.uuid4(), uuid.uuid4(), "some CV text")


@pytest.mark.asyncio
async def test_delete_cv_returns_true_when_the_repository_deleted_a_row() -> None:
    user_id, cv_document_id = uuid.uuid4(), uuid.uuid4()
    service = CvService(
        _FakeCandidateRepository(deletable_cv_ids={cv_document_id}), llm_provider=None
    )  # type: ignore[arg-type]

    assert await service.delete_cv(user_id, cv_document_id) is True


@pytest.mark.asyncio
async def test_delete_cv_returns_false_for_a_cv_that_does_not_belong_to_this_user() -> None:
    service = CvService(_FakeCandidateRepository(), llm_provider=None)  # type: ignore[arg-type]

    assert await service.delete_cv(uuid.uuid4(), uuid.uuid4()) is False


class _FailingLlmProvider:
    async def structured_completion(self, prompt, schema):
        raise RuntimeError("groq: 401 invalid api key gsk_secret account acct_42")


@pytest.mark.asyncio
async def test_a_provider_failure_never_reaches_the_caller_verbatim() -> None:
    # CV analysis answers straight back over HTTP, so the provider's own message
    # (account ids, rate-limit headers, keys) must not travel with the error —
    # see app/services/ai_errors.py.
    service = CvService(_FakeCandidateRepository(), llm_provider=_FailingLlmProvider())  # type: ignore[arg-type]

    with pytest.raises(LlmCallFailed) as raised:
        await service.analyze_cv(uuid.uuid4(), uuid.uuid4(), "CV text")

    assert "gsk_secret" not in str(raised.value)
    assert "acct_42" not in str(raised.value)


@pytest.mark.asyncio
async def test_user_corrections_survive_re_analysis() -> None:
    # The CV still says Django and says Python is "strong"; the user has already
    # said otherwise. Re-reading the CV must not quietly undo either decision.
    overrides = [
        SkillOverride(skill_key="django", name="Django", removed=True),
        SkillOverride(skill_key="python", name="Python", level=SkillLevel.EXPERT, years=6.0),
        SkillOverride(skill_key="rust", name="Rust", level=SkillLevel.COMMERCIAL),
    ]
    repository = _FakeCandidateRepository(skill_overrides=overrides)
    service = CvService(repository, llm_provider=_FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]

    profile = await service.analyze_cv(uuid.uuid4(), uuid.uuid4(), "some CV text")

    by_name = {skill.name: skill for skill in profile.skills}
    assert "Django" not in by_name
    assert by_name["Python"].level is SkillLevel.EXPERT
    assert by_name["Python"].years == 6.0
    assert by_name["Python"].source is SkillSource.USER
    assert by_name["Rust"].source is SkillSource.USER
