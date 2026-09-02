import uuid

import pytest

from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob
from app.services.embedding_service import EmbeddingService


def _job(description: str = "Build APIs.") -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://dou.ua/jobs/1",
        title="Backend Engineer",
        company="Acme",
        description=description,
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
    )


class _FakeVoyage:
    def __init__(self, fail_after: int | None = None):
        self.embedding_model = "voyage-test"
        self.rerank_model = "rerank-test"
        self.calls = 0
        self.embedded: list[str] = []
        self._fail_after = fail_after

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self._fail_after is not None and self.calls > self._fail_after:
            raise RuntimeError("voyage down")
        self.embedded.extend(texts)
        return [[1.0, 0.0] for _ in texts]


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, uuid.UUID, str], str] = {}

    async def stored_hashes(self, document_type, model, document_ids):
        return {
            document_id: content_hash
            for (stored_type, document_id, stored_model), content_hash in self.rows.items()
            if stored_type == document_type
            and stored_model == model
            and document_id in document_ids
        }

    async def save_vector(self, document_type, document_id, model, content_hash, vector):
        self.rows[(document_type, document_id, model)] = content_hash

    async def get_vector(self, document_type, document_id, model):
        return [1.0, 0.0] if (document_type, document_id, model) in self.rows else None


class _FakeJobs:
    def __init__(self, jobs: dict[uuid.UUID, NormalizedJob]):
        self.jobs = jobs

    async def list_all_canonical_job_ids(self):
        return list(self.jobs)

    async def list_normalized_jobs_for_canonical(self, canonical_job_ids):
        return {job_id: self.jobs[job_id] for job_id in canonical_job_ids if job_id in self.jobs}


def _service(jobs, voyage, embeddings=None):
    embeddings = embeddings or _FakeEmbeddings()
    return EmbeddingService(embeddings, _FakeJobs(jobs), voyage), embeddings


@pytest.mark.asyncio
async def test_every_job_is_embedded_on_a_first_pass() -> None:
    jobs = {uuid.uuid4(): _job(f"Build APIs {index}.") for index in range(3)}
    voyage = _FakeVoyage()
    service, _ = _service(jobs, voyage)

    result = await service.index_jobs()

    assert result.embedded == 3
    assert result.unchanged == 0
    assert result.complete is True


@pytest.mark.asyncio
async def test_a_second_pass_over_unchanged_jobs_costs_no_api_call() -> None:
    """The pipeline runs on a timer over a mostly-static corpus. Re-embedding what
    hasn't changed would be the single largest recurring cost in the system."""
    jobs = {uuid.uuid4(): _job()}
    voyage = _FakeVoyage()
    service, _ = _service(jobs, voyage)

    await service.index_jobs()
    calls_after_first = voyage.calls
    result = await service.index_jobs()

    assert voyage.calls == calls_after_first
    assert result.embedded == 0
    assert result.unchanged == 1


@pytest.mark.asyncio
async def test_a_changed_posting_is_re_embedded() -> None:
    job_id = uuid.uuid4()
    jobs = {job_id: _job("Build APIs.")}
    voyage = _FakeVoyage()
    service, _ = _service(jobs, voyage)
    await service.index_jobs()

    jobs[job_id] = _job("Now with Kubernetes.")
    result = await service.index_jobs()

    assert result.embedded == 1


@pytest.mark.asyncio
async def test_changing_the_model_invalidates_every_vector() -> None:
    """Vectors from two models are not comparable, so the whole corpus has to be
    rebuilt — and the count has to show it, or an empty jobs list looks like an
    outage."""
    jobs = {uuid.uuid4(): _job()}
    voyage = _FakeVoyage()
    service, _ = _service(jobs, voyage)
    await service.index_jobs()

    voyage.embedding_model = "voyage-other"
    result = await service.index_jobs()

    assert result.embedded == 1
    assert result.unchanged == 0


@pytest.mark.asyncio
async def test_a_failed_batch_is_reported_and_left_for_the_next_pass() -> None:
    jobs = {uuid.uuid4(): _job(f"Build APIs {index}.") for index in range(2)}
    voyage = _FakeVoyage(fail_after=0)
    service, _ = _service(jobs, voyage)

    result = await service.index_jobs()

    assert result.failed == 2
    assert result.embedded == 0
    assert result.complete is False


@pytest.mark.asyncio
async def test_limit_caps_one_pass_so_a_big_corpus_can_be_done_in_chunks() -> None:
    jobs = {uuid.uuid4(): _job(f"Build APIs {index}.") for index in range(5)}
    service, _ = _service(jobs, _FakeVoyage())

    result = await service.index_jobs(limit=2)

    assert result.embedded == 2
    assert result.total == 5


@pytest.mark.asyncio
async def test_index_profile_returns_the_same_text_the_reranker_will_be_given() -> None:
    """The document is returned rather than rebuilt by the caller: two
    constructions of "the same" text is exactly how an embedding and a rerank
    query quietly drift apart."""
    service, _ = _service({}, _FakeVoyage())

    document, embedded = await service.index_profile(uuid.uuid4(), "15 years of Python.", None)

    assert document == "CV: 15 years of Python."
    assert embedded is True


@pytest.mark.asyncio
async def test_an_unchanged_profile_is_not_re_embedded() -> None:
    user_id = uuid.uuid4()
    voyage = _FakeVoyage()
    service, _ = _service({}, voyage)

    await service.index_profile(user_id, "15 years of Python.", None)
    _, embedded = await service.index_profile(user_id, "15 years of Python.", None)

    assert embedded is False
