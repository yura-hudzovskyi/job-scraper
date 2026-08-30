import uuid

import pytest

from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob
from app.integrations.ai.llm.base import LLMResult
from app.services.job_skill_extraction_service import JobSkillExtractionService


class _FakeLlmProvider:
    def __init__(self, payload: dict):
        self._payload = payload

    async def structured_completion(self, prompt, schema):
        assert "Backend Engineer" in prompt
        return LLMResult(data=schema(**self._payload), model_label="fake-model")


class _FakeJobRepository:
    def __init__(self, job: NormalizedJob | None):
        self._job = job
        self.saved: tuple[uuid.UUID, list, str | None] | None = None

    async def get_normalized_job_for_canonical(self, canonical_job_id: uuid.UUID) -> NormalizedJob | None:
        return self._job

    async def update_skills_for_canonical(
        self, canonical_job_id: uuid.UUID, skills: list, generated_by: str | None
    ) -> None:
        self.saved = (canonical_job_id, skills, generated_by)


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


_EXTRACTED_PAYLOAD = {
    "skills": [
        {"name": "Django", "required": True},
        {"name": "PostgreSQL", "required": True},
        {"name": "Docker", "required": False},
    ]
}


@pytest.mark.asyncio
async def test_extract_and_save_maps_llm_output_and_persists() -> None:
    repository = _FakeJobRepository(_job())
    service = JobSkillExtractionService(repository, _FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]
    canonical_job_id = uuid.uuid4()

    skills = await service.extract_and_save(canonical_job_id)

    assert skills is not None
    assert [(s.name, s.required) for s in skills] == [
        ("Django", True),
        ("PostgreSQL", True),
        ("Docker", False),
    ]
    assert repository.saved == (canonical_job_id, skills, "fake-model")


@pytest.mark.asyncio
async def test_extract_and_save_without_llm_provider_is_a_no_op() -> None:
    repository = _FakeJobRepository(_job())
    service = JobSkillExtractionService(repository, llm_provider=None)  # type: ignore[arg-type]

    result = await service.extract_and_save(uuid.uuid4())

    assert result is None
    assert repository.saved is None


@pytest.mark.asyncio
async def test_extract_and_save_raises_when_job_not_found() -> None:
    repository = _FakeJobRepository(None)
    service = JobSkillExtractionService(repository, _FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]

    with pytest.raises(LookupError):
        await service.extract_and_save(uuid.uuid4())
