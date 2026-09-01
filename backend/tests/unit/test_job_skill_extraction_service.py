import uuid

import pytest

from app.domain.categories import JobCategory
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, RequirementType
from app.integrations.ai.llm.base import LLMResult
from app.services.job_skill_extraction_service import JobSkillExtractionService

_DESCRIPTION = (
    "We use Django and PostgreSQL. Docker is a nice to have. "
    "Experience with Postgres tuning is required."
)


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
        self.saved_category: tuple[JobCategory | None, float | None] | None = None

    async def get_normalized_job_for_canonical(self, canonical_job_id: uuid.UUID) -> NormalizedJob | None:
        return self._job

    async def update_skills_for_canonical(
        self,
        canonical_job_id: uuid.UUID,
        skills: list,
        generated_by: str | None,
        category: JobCategory | None = None,
        category_confidence: float | None = None,
    ) -> None:
        self.saved = (canonical_job_id, skills, generated_by)
        self.saved_category = (category, category_confidence)


def _job() -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title="Backend Engineer",
        company="Acme",
        description=_DESCRIPTION,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        salary=None,
        seniority=None,
        required_experience_years=None,
    )


_EXTRACTED_PAYLOAD = {
    "skills": [
        {
            "name": "Django",
            "requirement": "required_explicit",
            "evidence": "We use Django and PostgreSQL.",
            "confidence": 0.9,
        },
        {
            "name": "PostgreSQL",
            "requirement": "required_explicit",
            "evidence": "We use Django and PostgreSQL.",
            "confidence": 0.9,
        },
        {
            "name": "Docker",
            "requirement": "optional_explicit",
            "evidence": "Docker is a nice to have.",
            "confidence": 0.8,
        },
    ],
    "category": "backend",
    "category_confidence": 0.85,
}


@pytest.mark.asyncio
async def test_extract_and_save_records_framing_evidence_and_category() -> None:
    repository = _FakeJobRepository(_job())
    service = JobSkillExtractionService(repository, _FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]
    canonical_job_id = uuid.uuid4()

    skills = await service.extract_and_save(canonical_job_id)

    assert skills is not None
    assert [(s.name, s.requirement, s.required) for s in skills] == [
        ("Django", RequirementType.REQUIRED_EXPLICIT, True),
        ("PostgreSQL", RequirementType.REQUIRED_EXPLICIT, True),
        ("Docker", RequirementType.OPTIONAL_EXPLICIT, False),
    ]
    assert skills[0].evidence == "We use Django and PostgreSQL."
    assert repository.saved == (canonical_job_id, skills, "fake-model")
    assert repository.saved_category == (JobCategory.BACKEND, 0.85)


@pytest.mark.asyncio
async def test_aliases_collapse_and_the_stronger_framing_wins() -> None:
    # The same posting calls it "PostgreSQL" in one line and "Postgres" in
    # another: one requirement, and a "nice to have" mention must not downgrade
    # the one that said "required".
    repository = _FakeJobRepository(_job())
    payload = {
        "skills": [
            {"name": "Postgres", "requirement": "optional_explicit", "evidence": None},
            {
                "name": "PostgreSQL",
                "requirement": "required_explicit",
                "evidence": "Experience with Postgres tuning is required.",
            },
        ]
    }
    service = JobSkillExtractionService(repository, _FakeLlmProvider(payload))  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4())

    assert skills is not None
    assert len(skills) == 1
    assert skills[0].name == "PostgreSQL"
    assert skills[0].canonical_id == "postgresql"
    assert skills[0].requirement is RequirementType.REQUIRED_EXPLICIT
    assert skills[0].evidence == "Experience with Postgres tuning is required."


@pytest.mark.asyncio
async def test_evidence_the_posting_does_not_contain_is_dropped() -> None:
    # A justification that isn't in the vacancy is a hallucination — the skill is
    # still recorded (the model may well be right), but nothing unverifiable is
    # stored as if the posting had said it.
    repository = _FakeJobRepository(_job())
    payload = {
        "skills": [
            {
                "name": "Kubernetes",
                "requirement": "required_explicit",
                "evidence": "Kubernetes experience is mandatory.",
            }
        ]
    }
    service = JobSkillExtractionService(repository, _FakeLlmProvider(payload))  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4())

    assert skills is not None
    assert skills[0].name == "Kubernetes"
    assert skills[0].evidence is None


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


class _FailingLlmProvider:
    async def structured_completion(self, prompt, schema):
        raise RuntimeError("provider unreachable")


@pytest.mark.asyncio
async def test_extract_and_save_returns_none_when_the_llm_call_fails() -> None:
    # Regression test: extract_job_skills.delay's Celery task fans out
    # score_job_for_user for every onboarded user right after extract_and_save
    # returns — if a bad LLM response (timeout, provider outage, a response that
    # doesn't match the schema) raised out of here instead of degrading to None,
    # the whole task would fail and silently skip scoring for every user on that
    # job. See job_skill_extraction_service.py's module docstring.
    repository = _FakeJobRepository(_job())
    service = JobSkillExtractionService(repository, _FailingLlmProvider())  # type: ignore[arg-type]

    result = await service.extract_and_save(uuid.uuid4())

    assert result is None
    assert repository.saved is None
