import uuid

import pytest

from app.domain.candidates.models import CandidateProfile
from app.integrations.ai.llm.base import LLMResult
from app.services.cv_service import CvService, LlmNotConfigured


class _FakeLlmProvider:
    def __init__(self, payload: dict):
        self._payload = payload

    async def structured_completion(self, prompt, schema):
        assert "CV" in prompt
        return LLMResult(data=schema(**self._payload), model_label="fake-model")


class _FakeCandidateRepository:
    def __init__(self, deletable_cv_ids: set[uuid.UUID] | None = None) -> None:
        self.saved: tuple[uuid.UUID, uuid.UUID, CandidateProfile] | None = None
        self._deletable_cv_ids = deletable_cv_ids or set()

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
