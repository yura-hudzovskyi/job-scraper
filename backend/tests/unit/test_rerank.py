"""Reranking is where "one model per run" is enforced, so the tests are mostly
about what must never happen: a mixed ranking, a partial one, or a raw score
escaping without calibration.
"""

import uuid

import httpx
import pytest

from app.domain.matching.calibration import calibrate_relevance
from app.domain.matching.rerank import (
    INSTRUCTION_VERSION,
    RERANK_INSTRUCTION,
    RerankService,
    rerank_query,
)
from app.integrations.ai.rerank.providers import (
    CloudflareRerankEngine,
    LocalCrossEncoderRerankEngine,
    VoyageRerankEngine,
)


class _FakeEngine:
    def __init__(self, model_id: str, scores: list[float] | None = None, error: Exception | None = None):
        self._model_id = model_id
        self._scores = scores or []
        self._error = error
        self.calls: list[tuple[str, list[str]]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        if self._error is not None:
            raise self._error
        return self._scores


def _documents(count: int) -> dict[uuid.UUID, str]:
    return {uuid.uuid4(): f"JOB {index}" for index in range(count)}


@pytest.mark.asyncio
async def test_the_first_working_engine_ranks_the_whole_set() -> None:
    documents = _documents(3)
    engine = _FakeEngine("voyage:rerank-3", scores=[0.2, 0.9, 0.5])
    service = RerankService([engine])  # type: ignore[list-item]

    run = await service.rerank("CANDIDATE", documents)

    assert run.model_id == "voyage:rerank-3"
    assert run.instruction_version == INSTRUCTION_VERSION
    assert [job.rank for job in run.jobs] == [1, 2, 3]
    assert [job.relevance for job in run.jobs] == [0.9, 0.5, 0.2]
    assert len(engine.calls[0][1]) == 3


@pytest.mark.asyncio
async def test_a_failed_engine_hands_the_entire_set_to_the_next_one() -> None:
    # Not the remainder — the whole set. Half a ranking from one model and half
    # from another is not a ranking.
    documents = _documents(4)
    broken = _FakeEngine("voyage:rerank-3", error=RuntimeError("429"))
    fallback = _FakeEngine("local:ms-marco", scores=[0.1, 0.2, 0.3, 0.4])
    service = RerankService([broken, fallback])  # type: ignore[list-item]

    run = await service.rerank("CANDIDATE", documents)

    assert run.model_id == "local:ms-marco"
    assert len(fallback.calls[0][1]) == 4
    assert len(run.jobs) == 4


@pytest.mark.asyncio
async def test_a_short_answer_is_discarded_rather_than_used() -> None:
    documents = _documents(3)
    short = _FakeEngine("voyage:rerank-3", scores=[0.9, 0.8])
    fallback = _FakeEngine("local:ms-marco", scores=[0.1, 0.2, 0.3])
    service = RerankService([short, fallback])  # type: ignore[list-item]

    run = await service.rerank("CANDIDATE", documents)

    assert run.model_id == "local:ms-marco"


@pytest.mark.asyncio
async def test_no_usable_engine_leaves_retrieval_order_alone() -> None:
    documents = _documents(2)
    service = RerankService([_FakeEngine("voyage:rerank-3", error=RuntimeError("down"))])  # type: ignore[list-item]

    run = await service.rerank("CANDIDATE", documents)

    assert run.ran is False
    assert run.jobs == []


@pytest.mark.asyncio
async def test_the_same_input_ranks_the_same_way_every_time() -> None:
    # Deterministic fallback ordering is what makes a rerun comparable; ties are
    # broken by id rather than by dict order.
    documents = _documents(5)
    service = RerankService([_FakeEngine("local:ms-marco", scores=[0.5] * 5)])  # type: ignore[list-item]

    first = await service.rerank("CANDIDATE", documents)
    second = await service.rerank("CANDIDATE", documents)

    assert [job.canonical_job_id for job in first.jobs] == [
        job.canonical_job_id for job in second.jobs
    ]


@pytest.mark.asyncio
async def test_a_logit_score_is_calibrated_before_anyone_sees_it() -> None:
    documents = _documents(2)
    service = RerankService([_FakeEngine("cloudflare:@cf/baai/bge-reranker-base", scores=[6.0, -6.0])])  # type: ignore[list-item]

    run = await service.rerank("CANDIDATE", documents)

    assert run.jobs[0].raw_score == 6.0
    assert 0.9 < run.jobs[0].relevance <= 1.0
    assert 0.0 <= run.jobs[1].relevance < 0.1


def test_the_query_carries_the_versioned_instruction() -> None:
    query = rerank_query("TARGET: Backend")

    assert query.startswith(RERANK_INSTRUCTION)
    assert "TARGET: Backend" in query


def test_a_bounded_provider_score_is_left_alone() -> None:
    assert calibrate_relevance("voyage:rerank-3", 0.42) == 0.42
    assert calibrate_relevance("voyage:rerank-3", 1.5) == 1.0


def test_calibration_is_monotone_so_ranking_survives_it() -> None:
    # Regression: deciding "this looks bounded already" per value rather than per
    # model mapped a raw -1 above a raw 0, quietly reversing two results.
    model = "cloudflare:@cf/baai/bge-reranker-base"
    values = [-8.0, -1.0, 0.0, 0.5, 2.0, 9.0]

    calibrated = [calibrate_relevance(model, value) for value in values]

    assert calibrated == sorted(calibrated)
    assert calibrate_relevance(model, -1.0) < calibrate_relevance(model, 0.0)


@pytest.mark.asyncio
async def test_voyage_puts_results_back_into_input_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.5},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = VoyageRerankEngine("pa-fake", client=client)

    assert await engine.rerank("q", ["a", "b", "c"]) == [0.1, 0.5, 0.9]


@pytest.mark.asyncio
async def test_cloudflare_rerank_unwraps_its_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {"response": [{"id": 1, "score": 3.0}, {"id": 0, "score": -1.0}]},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    engine = CloudflareRerankEngine("acct", "cf-fake", client=client)

    assert await engine.rerank("q", ["a", "b"]) == [-1.0, 3.0]


@pytest.mark.asyncio
async def test_the_local_engine_reuses_the_cross_encoder() -> None:
    class _FakeCrossEncoder:
        def __init__(self) -> None:
            self.pairs: list[tuple[str, str]] = []

        async def score(self, pairs):
            self.pairs = pairs
            return [0.7 for _ in pairs]

    cross_encoder = _FakeCrossEncoder()
    engine = LocalCrossEncoderRerankEngine(cross_encoder, "ms-marco")  # type: ignore[arg-type]

    scores = await engine.rerank("query", ["a", "b"])

    assert scores == [0.7, 0.7]
    assert cross_encoder.pairs == [("query", "a"), ("query", "b")]
    assert engine.model_id == "local:ms-marco"
