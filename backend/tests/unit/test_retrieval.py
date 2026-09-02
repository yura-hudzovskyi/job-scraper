"""Retrieval decides what the expensive stages ever get to see, so the properties
that matter are: it never queries a lane that can't answer, it never mixes lanes,
and a category classifier can rank a vacancy down but not make it disappear.
"""

import uuid

import pytest

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    SkillLevel,
    UserPreference,
)
from app.domain.categories import CategoryDecision, JobCategory
from app.domain.matching.documents import Section
from app.domain.matching.retrieval import SECTION_WEIGHTS, RetrievalService
from app.integrations.ai.embeddings.lanes import DURABLE, QUALITY, LaneSpec
from app.repositories.embedding_repository import Candidate, EmbeddingLane


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1, 0.2] for _ in texts]


class _FakeEmbeddingRepository:
    def __init__(self, lanes: list[EmbeddingLane], candidates: list[Candidate]):
        self._lanes = lanes
        self._candidates = candidates
        self.searched: list[tuple[str, list]] = []

    async def list_lanes(self):
        return list(self._lanes)

    async def search(self, lane_id, document_type, queries, limit, candidate_ids=None):
        self.searched.append((lane_id, queries))
        return self._candidates[:limit]


class _FakeCategories:
    def __init__(self, categories: dict[uuid.UUID, tuple[JobCategory, float]]):
        self._categories = categories

    async def categories_for(self, canonical_job_ids):
        return {
            job_id: self._categories[job_id]
            for job_id in canonical_job_ids
            if job_id in self._categories
        }


def _lane_row(lane_id: str, role: str, state: str) -> EmbeddingLane:
    return EmbeddingLane(
        id=lane_id, provider=lane_id.split(":")[0], model="m", dimension=2, role=role, state=state
    )


def _lane_spec(provider: str, role: str, embedder: _FakeProvider) -> LaneSpec:
    return LaneSpec(provider=provider, model="m", role=role, build=lambda: embedder)


def _profile() -> CandidateProfile:
    return CandidateProfile(
        id=str(uuid.uuid4()),
        user_id="u1",
        experience_years=4.0,
        roles=["Backend Engineer"],
        skills=[CandidateSkill(name="Python", level=SkillLevel.STRONG)],
    )


@pytest.mark.asyncio
async def test_nothing_is_retrieved_until_a_lane_is_ready() -> None:
    # A lane at 60% coverage doesn't return worse results, it returns a smaller
    # world — the caller has to be able to tell that apart from "no matches".
    embedder = _FakeProvider()
    repository = _FakeEmbeddingRepository([_lane_row("local:m:v1", DURABLE, "building")], [])
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({}),  # type: ignore[arg-type]
        [_lane_spec("local", DURABLE, embedder)],
    )

    result = await service.retrieve(_profile())

    assert result.usable is False
    assert result.jobs == []
    assert embedder.calls == []


@pytest.mark.asyncio
async def test_the_ready_quality_lane_is_preferred_and_used_alone() -> None:
    quality, durable = _FakeProvider(), _FakeProvider()
    repository = _FakeEmbeddingRepository(
        [_lane_row("local:m:v1", DURABLE, "ready"), _lane_row("voyage:m:v1", QUALITY, "ready")],
        [Candidate(document_id=uuid.uuid4(), score=0.8)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({}),  # type: ignore[arg-type]
        [_lane_spec("voyage", QUALITY, quality), _lane_spec("local", DURABLE, durable)],
    )

    result = await service.retrieve(_profile())

    assert result.lane_id == "voyage:m:v1"
    # One lane answers the whole query — mixing two vector spaces is the bug this
    # design exists to prevent.
    assert [lane_id for lane_id, _ in repository.searched] == ["voyage:m:v1"]
    assert quality.calls and durable.calls == []


@pytest.mark.asyncio
async def test_a_building_quality_lane_falls_back_to_the_durable_one() -> None:
    quality, durable = _FakeProvider(), _FakeProvider()
    repository = _FakeEmbeddingRepository(
        [_lane_row("voyage:m:v1", QUALITY, "building"), _lane_row("local:m:v1", DURABLE, "ready")],
        [Candidate(document_id=uuid.uuid4(), score=0.5)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({}),  # type: ignore[arg-type]
        [_lane_spec("voyage", QUALITY, quality), _lane_spec("local", DURABLE, durable)],
    )

    result = await service.retrieve(_profile())

    assert result.lane_id == "local:m:v1"
    assert quality.calls == []


@pytest.mark.asyncio
async def test_each_weighted_section_becomes_its_own_query() -> None:
    repository = _FakeEmbeddingRepository([_lane_row("local:m:v1", DURABLE, "ready")], [])
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({}),  # type: ignore[arg-type]
        [_lane_spec("local", DURABLE, _FakeProvider())],
    )

    await service.retrieve(_profile())

    [(_, queries)] = repository.searched
    assert {query.section for query in queries} <= {section.value for section in Section}
    assert all(query.weight > 0 for query in queries)
    assert queries[0].weight == SECTION_WEIGHTS[Section(queries[0].section)]


@pytest.mark.asyncio
async def test_a_soft_mismatch_is_ranked_down_not_removed() -> None:
    adjacent, exact = uuid.uuid4(), uuid.uuid4()
    repository = _FakeEmbeddingRepository(
        [_lane_row("local:m:v1", DURABLE, "ready")],
        [Candidate(document_id=adjacent, score=0.90), Candidate(document_id=exact, score=0.85)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories(  # type: ignore[arg-type]
            {adjacent: (JobCategory.QA, 0.9), exact: (JobCategory.BACKEND, 0.9)}
        ),
        [_lane_spec("local", DURABLE, _FakeProvider())],
    )

    result = await service.retrieve(_profile())

    assert [job.canonical_job_id for job in result.jobs] == [exact, adjacent]
    assert result.jobs[1].category is CategoryDecision.SOFT_MISMATCH
    assert result.jobs[1].score < 0.90


@pytest.mark.asyncio
async def test_a_hard_mismatch_still_reaches_the_exploration_slice() -> None:
    # The whole point of the slice: a mislabelled or genuinely cross-functional
    # vacancy is ranked last, not made invisible.
    sales, backend = uuid.uuid4(), uuid.uuid4()
    repository = _FakeEmbeddingRepository(
        [_lane_row("local:m:v1", DURABLE, "ready")],
        [Candidate(document_id=sales, score=0.95), Candidate(document_id=backend, score=0.60)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories(  # type: ignore[arg-type]
            {sales: (JobCategory.SALES, 0.95), backend: (JobCategory.BACKEND, 0.9)}
        ),
        [_lane_spec("local", DURABLE, _FakeProvider())],
    )

    result = await service.retrieve(_profile(), limit=10)

    by_id = {job.canonical_job_id: job for job in result.jobs}
    assert by_id[backend].exploration is False
    assert by_id[sales].exploration is True
    assert by_id[sales].category is CategoryDecision.HARD_MISMATCH
    # Ranked below the on-target job despite scoring higher on raw similarity.
    assert [job.canonical_job_id for job in result.jobs] == [backend, sales]


@pytest.mark.asyncio
async def test_an_unclassified_job_is_never_ruled_out() -> None:
    unknown = uuid.uuid4()
    repository = _FakeEmbeddingRepository(
        [_lane_row("local:m:v1", DURABLE, "ready")],
        [Candidate(document_id=unknown, score=0.7)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({}),  # type: ignore[arg-type]
        [_lane_spec("local", DURABLE, _FakeProvider())],
    )

    result = await service.retrieve(_profile())

    assert [job.category for job in result.jobs] == [CategoryDecision.PASS]


@pytest.mark.asyncio
async def test_already_seen_jobs_are_excluded() -> None:
    seen, fresh = uuid.uuid4(), uuid.uuid4()
    repository = _FakeEmbeddingRepository(
        [_lane_row("local:m:v1", DURABLE, "ready")],
        [Candidate(document_id=seen, score=0.9), Candidate(document_id=fresh, score=0.4)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({}),  # type: ignore[arg-type]
        [_lane_spec("local", DURABLE, _FakeProvider())],
    )

    result = await service.retrieve(_profile(), exclude_ids={seen})

    assert [job.canonical_job_id for job in result.jobs] == [fresh]


@pytest.mark.asyncio
async def test_preferred_roles_decide_what_counts_as_on_target() -> None:
    # The candidate wants QA work even though their CV says backend; a QA vacancy
    # must not be penalised for that.
    qa_job = uuid.uuid4()
    repository = _FakeEmbeddingRepository(
        [_lane_row("local:m:v1", DURABLE, "ready")],
        [Candidate(document_id=qa_job, score=0.8)],
    )
    service = RetrievalService(
        repository,  # type: ignore[arg-type]
        _FakeCategories({qa_job: (JobCategory.QA, 0.95)}),  # type: ignore[arg-type]
        [_lane_spec("local", DURABLE, _FakeProvider())],
    )
    preferences = UserPreference(
        user_id="u1", desired_salary_usd=None, preferred_roles=["QA Automation Engineer"]
    )

    result = await service.retrieve(_profile(), preferences)

    assert result.jobs[0].category is CategoryDecision.PASS
    assert result.jobs[0].score == 0.8
