"""Indexing has to be cheap to re-run, honest about partial failure, and honest
about when a lane can actually answer a query.
"""

import uuid

import pytest

from app.domain.candidates.models import CandidateProfile, CandidateSkill, SkillLevel
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob, NormalizedJobSkill
from app.integrations.ai.embeddings.lanes import DURABLE, QUALITY, LaneSpec
from app.repositories.embedding_repository import JOB, EmbeddingLane, SectionVector
from app.services.embedding_indexing_service import (
    BUILDING,
    READY,
    EmbeddingIndexingService,
)


class _FakeProvider:
    def __init__(self, dimension: int = 3, error: Exception | None = None, short: bool = False):
        self.calls: list[list[str]] = []
        self._dimension = dimension
        self._error = error
        self._short = short

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self._error is not None:
            raise self._error
        vectors = [[0.1] * self._dimension for _ in texts]
        return vectors[:-1] if self._short and vectors else vectors


class _FakeRepository:
    def __init__(self, lanes: list[EmbeddingLane] | None = None, covered: int = 0):
        self.saved: list[tuple[str, str, SectionVector]] = []
        self.lanes = lanes or []
        self.states: dict[str, str] = {}
        self._covered = covered
        self._hashes: dict[tuple[str, str], dict[str, str]] = {}

    async def stored_hashes(self, document_type: str, document_id: uuid.UUID, lane_id: str):
        return dict(self._hashes.get((str(document_id), lane_id), {}))

    async def upsert_lane(self, lane: EmbeddingLane) -> None:
        self.lanes = [existing for existing in self.lanes if existing.id != lane.id] + [lane]

    async def save_vectors(self, document_type, document_id, version, lane_id, vectors):
        stored = self._hashes.setdefault((str(document_id), lane_id), {})
        for vector in vectors:
            stored[vector.section] = vector.content_hash
            self.saved.append((lane_id, vector.section, vector))
        return len(vectors)

    async def list_lanes(self):
        return list(self.lanes)

    async def documents_with_vectors(self, lane_id: str, document_type: str) -> int:
        return self._covered

    async def set_lane_state(self, lane_id: str, state: str) -> None:
        self.states[lane_id] = state


def _lane(name: str, provider: _FakeProvider, role: str = DURABLE) -> LaneSpec:
    return LaneSpec(provider=name, model="m", role=role, build=lambda: provider)


def _job() -> NormalizedJob:
    return NormalizedJob(
        source="dou",
        external_id="1",
        url="https://example.com/1",
        title="Backend Engineer",
        company="Acme",
        description="Own the payments API.",
        employment_type=EmploymentType.FULL_TIME,
        location=JobLocation(remote=True),
        salary=None,
        seniority="senior",
        required_experience_years=5.0,
        skills=[NormalizedJobSkill(name="Python")],
    )


def _profile() -> CandidateProfile:
    return CandidateProfile(
        id=str(uuid.uuid4()),
        user_id="u1",
        experience_years=4.0,
        roles=["Backend Engineer"],
        skills=[CandidateSkill(name="Python", level=SkillLevel.STRONG)],
        version=3,
    )


@pytest.mark.asyncio
async def test_every_configured_lane_gets_its_own_vectors() -> None:
    quality, durable = _FakeProvider(dimension=4), _FakeProvider(dimension=3)
    repository = _FakeRepository()
    service = EmbeddingIndexingService(
        repository,  # type: ignore[arg-type]
        [_lane("voyage", quality, QUALITY), _lane("local", durable)],
    )

    results = await service.index_job(uuid.uuid4(), _job(), version=1)

    assert [result.written for result in results] == [len(quality.calls[0]), len(durable.calls[0])]
    assert {lane_id for lane_id, _, _ in repository.saved} == {"voyage:m:v1", "local:m:v1"}
    # The dimension is whatever the model actually produced, not a declared guess.
    assert {lane.id: lane.dimension for lane in repository.lanes} == {
        "voyage:m:v1": 4,
        "local:m:v1": 3,
    }


@pytest.mark.asyncio
async def test_unchanged_sections_are_not_embedded_again() -> None:
    # A re-scrape that moved nothing a match depends on must cost no provider call.
    provider = _FakeProvider()
    repository = _FakeRepository()
    service = EmbeddingIndexingService(repository, [_lane("local", provider)])  # type: ignore[arg-type]
    job_id = uuid.uuid4()

    first = await service.index_job(job_id, _job(), version=1)
    second = await service.index_job(job_id, _job(), version=1)

    assert first[0].written > 0
    assert second[0].written == 0
    assert second[0].unchanged == first[0].written
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_only_the_changed_section_is_re_embedded() -> None:
    provider = _FakeProvider()
    repository = _FakeRepository()
    service = EmbeddingIndexingService(repository, [_lane("local", provider)])  # type: ignore[arg-type]
    job_id = uuid.uuid4()
    await service.index_job(job_id, _job(), version=1)

    from dataclasses import replace

    edited = replace(_job(), description="Own the payments API and mentor two juniors.")
    result = await service.index_job(job_id, edited, version=2)

    assert result[0].written == 1
    assert len(provider.calls[1]) == 1


@pytest.mark.asyncio
async def test_one_unreachable_lane_does_not_stop_the_others() -> None:
    broken = _FakeProvider(error=RuntimeError("provider down"))
    working = _FakeProvider()
    repository = _FakeRepository()
    service = EmbeddingIndexingService(
        repository,  # type: ignore[arg-type]
        [_lane("voyage", broken, QUALITY), _lane("local", working)],
    )

    results = await service.index_job(uuid.uuid4(), _job(), version=1)

    assert results[0].failed is True
    assert results[1].written > 0
    assert {lane_id for lane_id, _, _ in repository.saved} == {"local:m:v1"}


@pytest.mark.asyncio
async def test_a_short_response_is_rejected_rather_than_misaligned() -> None:
    # Zipping fewer vectors than sections would silently attach one section's
    # vector to another's label.
    provider = _FakeProvider(short=True)
    repository = _FakeRepository()
    service = EmbeddingIndexingService(repository, [_lane("local", provider)])  # type: ignore[arg-type]

    [result] = await service.index_job(uuid.uuid4(), _job(), version=1)

    assert result.failed is True
    assert repository.saved == []


@pytest.mark.asyncio
async def test_a_profile_is_indexed_under_its_own_revision() -> None:
    provider = _FakeProvider()
    repository = _FakeRepository()
    service = EmbeddingIndexingService(repository, [_lane("local", provider)])  # type: ignore[arg-type]

    results = await service.index_profile(_profile())

    assert results[0].written > 0


@pytest.mark.asyncio
async def test_a_lane_becomes_ready_only_once_it_covers_the_corpus() -> None:
    lane = EmbeddingLane(
        id="local:m:v1", provider="local", model="m", dimension=3, role=DURABLE, state=BUILDING
    )
    repository = _FakeRepository(lanes=[lane], covered=99)
    service = EmbeddingIndexingService(repository, [])  # type: ignore[arg-type]

    states = await service.refresh_lane_readiness(active_job_count=100)

    assert states == {"local:m:v1": READY}
    assert repository.states == {"local:m:v1": READY}


@pytest.mark.asyncio
async def test_a_half_built_lane_stays_building() -> None:
    # A lane at 60% doesn't return worse results, it returns a smaller world —
    # much harder to notice than an outage.
    lane = EmbeddingLane(
        id="local:m:v1", provider="local", model="m", dimension=3, role=DURABLE, state=READY
    )
    repository = _FakeRepository(lanes=[lane], covered=60)
    service = EmbeddingIndexingService(repository, [])  # type: ignore[arg-type]

    states = await service.refresh_lane_readiness(active_job_count=100)

    assert states == {"local:m:v1": BUILDING}


@pytest.mark.asyncio
async def test_an_empty_corpus_never_declares_a_lane_ready() -> None:
    lane = EmbeddingLane(
        id="local:m:v1", provider="local", model="m", dimension=3, role=DURABLE, state=BUILDING
    )
    repository = _FakeRepository(lanes=[lane], covered=0)
    service = EmbeddingIndexingService(repository, [])  # type: ignore[arg-type]

    assert await service.refresh_lane_readiness(active_job_count=0) == {"local:m:v1": BUILDING}
    assert JOB == "job"
