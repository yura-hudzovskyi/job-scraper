import uuid
from dataclasses import replace

import pytest

from app.domain.categories import JobCategory
from app.domain.jobs.models import (
    EmploymentType,
    JobLocation,
    NormalizedJob,
    NormalizedJobSkill,
    RequirementType,
)
from app.domain.skills import rule_extractor
from app.domain.versioning import job_posting_hash
from app.integrations.ai.llm.base import LLMResult
from app.services.job_skill_extraction_service import EXTRACTION_VERSION, JobSkillExtractionService

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
    def __init__(self, job: NormalizedJob | None, extraction_key: str | None = None):
        self._job = job
        self.extraction_key = extraction_key
        self.saved: tuple[uuid.UUID, list, str | None] | None = None
        self.saved_category: tuple[JobCategory | None, float | None] | None = None
        self.saved_extraction_key: str | None = None

    async def get_normalized_job_for_canonical(self, canonical_job_id: uuid.UUID) -> NormalizedJob | None:
        return self._job

    async def get_extraction_key(self, canonical_job_id: uuid.UUID) -> str | None:
        return self.extraction_key

    async def update_skills_for_canonical(
        self,
        canonical_job_id: uuid.UUID,
        skills: list,
        generated_by: str | None,
        category: JobCategory | None = None,
        category_confidence: float | None = None,
        extraction_key: str | None = None,
    ) -> None:
        self.saved = (canonical_job_id, skills, generated_by)
        self.saved_category = (category, category_confidence)
        self.saved_extraction_key = extraction_key


def _named(skills, name):
    return next((skill for skill in skills if skill.name == name), None)


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
async def test_without_an_llm_the_rules_extractor_still_produces_requirements() -> None:
    # The whole point of the fallback: a job reaches scoring with real
    # requirements instead of an empty list that would score as "nothing checked".
    repository = _FakeJobRepository(_job())
    service = JobSkillExtractionService(repository, llm_provider=None)  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4())

    assert {skill.name for skill in skills} == {"Django", "PostgreSQL", "Docker"}
    assert _named(skills, "Docker").requirement is RequirementType.OPTIONAL_EXPLICIT
    assert repository.saved is not None
    assert repository.saved[2] == rule_extractor.EXTRACTOR_LABEL
    # Rules can't classify a role — better no category than a guessed one.
    assert repository.saved_category == (None, None)


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
async def test_a_failed_llm_call_degrades_to_rules_instead_of_failing_the_task() -> None:
    # extract_job_skills.delay fans out score_job_for_user for every onboarded
    # user right after this returns, so a bad LLM response (timeout, provider
    # outage, a response that doesn't match the schema) must not propagate — it
    # would fail the whole Celery task and silently skip scoring for every user
    # on that job.
    repository = _FakeJobRepository(_job())
    service = JobSkillExtractionService(repository, _FailingLlmProvider())  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4())

    assert {skill.name for skill in skills} == {"Django", "PostgreSQL", "Docker"}
    assert repository.saved is not None
    assert repository.saved[2] == rule_extractor.EXTRACTOR_LABEL


@pytest.mark.asyncio
async def test_a_rules_fallback_never_overwrites_an_existing_llm_extraction() -> None:
    # A rescore run whose LLM leg is down must not replace richer, model-read
    # requirements with what a dictionary scan found.
    already_extracted = [
        NormalizedJobSkill(
            name="Kubernetes",
            requirement=RequirementType.REQUIRED_EXPLICIT,
            canonical_id="kubernetes",
        )
    ]
    job = replace(_job(), skills=already_extracted, skills_extracted_by="Groq (some-model)")
    repository = _FakeJobRepository(job)
    service = JobSkillExtractionService(repository, _FailingLlmProvider())  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4())

    assert skills == already_extracted
    assert repository.saved is None


class _NeverCalledLlmProvider:
    async def structured_completion(self, prompt, schema):
        raise AssertionError("the posting hasn't changed — it must not be read again")


def _current_key(job: NormalizedJob) -> str:
    return f"{job_posting_hash(job)}:{EXTRACTION_VERSION}"


@pytest.mark.asyncio
async def test_an_unchanged_posting_is_not_read_again() -> None:
    # Spending a request to re-derive an answer already on the row is exactly the
    # quota this pipeline can't afford (docs/ai-pipeline-v3.md, 8).
    job = replace(_job(), skills=[NormalizedJobSkill(name="Django")], skills_extracted_by="fake-model")
    repository = _FakeJobRepository(job, extraction_key=_current_key(job))
    service = JobSkillExtractionService(repository, _NeverCalledLlmProvider())  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4())

    assert skills == job.skills
    assert repository.saved is None


@pytest.mark.asyncio
async def test_force_re_reads_a_posting_that_has_not_changed() -> None:
    # "Rescore all vacancies" is an explicit refresh — e.g. after switching
    # models — so it must not be answered from the cache.
    job = _job()
    repository = _FakeJobRepository(job, extraction_key=_current_key(job))
    service = JobSkillExtractionService(repository, _FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]

    skills = await service.extract_and_save(uuid.uuid4(), force=True)

    assert [skill.name for skill in skills] == ["Django", "PostgreSQL", "Docker"]
    assert repository.saved is not None


@pytest.mark.asyncio
async def test_an_edited_posting_is_read_again() -> None:
    job = _job()
    stale_key = f"{job_posting_hash(replace(job, description='Old text.'))}:{EXTRACTION_VERSION}"
    repository = _FakeJobRepository(job, extraction_key=stale_key)
    service = JobSkillExtractionService(repository, _FakeLlmProvider(_EXTRACTED_PAYLOAD))  # type: ignore[arg-type]

    await service.extract_and_save(uuid.uuid4())

    assert repository.saved is not None
    assert repository.saved_extraction_key == _current_key(job)
