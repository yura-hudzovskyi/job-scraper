import uuid
from dataclasses import replace

import pytest

from app.domain.candidates.models import CvDocument, UserPreference
from app.domain.jobs.models import EmploymentType, JobLocation, NormalizedJob
from app.domain.matching.models import Recommendation
from app.domain.pipeline_config import DEFAULTS
from app.repositories.embedding_repository import Candidate
from app.services.matching_service import MatchingService

_USER_ID = uuid.uuid4()


def _job(title: str = "Backend Engineer", company: str = "Acme", **overrides) -> NormalizedJob:
    defaults = {
        "source": "dou",
        "external_id": title,
        "url": "https://dou.ua/jobs/1",
        "title": title,
        "company": company,
        "description": "Build APIs.",
        "employment_type": EmploymentType.FULL_TIME,
        "location": JobLocation(remote=True),
    }
    defaults.update(overrides)
    return NormalizedJob(**defaults)


class _FakeVoyage:
    def __init__(self, relevance: dict[str, float] | None = None, rerank_fails: bool = False):
        self.embedding_model = "voyage-test"
        self.rerank_model = "rerank-test"
        self._relevance = relevance or {}
        self._rerank_fails = rerank_fails
        self.rerank_calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.rerank_calls.append(documents)
        if self._rerank_fails:
            raise RuntimeError("voyage down")
        return [self._relevance.get(_title_of(document), 0.5) for document in documents]


def _title_of(document: str) -> str:
    return document.splitlines()[0].removeprefix("TITLE: ")


class _FakeCandidates:
    def __init__(self, cv_text: str | None, preferences: UserPreference | None = None):
        self._cv_text = cv_text
        self._preferences = preferences

    async def get_active_cv(self, user_id):
        if self._cv_text is None:
            return None
        return CvDocument(
            id="cv1",
            user_id=str(user_id),
            filename="cv.txt",
            raw_text=self._cv_text,
            uploaded_at=None,
        )

    async def get_preferences(self, user_id):
        return self._preferences


class _FakeEmbeddings:
    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates
        self.saved: list[tuple] = []
        self._hashes: dict = {}

    async def stored_hashes(self, document_type, model, document_ids):
        return self._hashes

    async def save_vector(self, document_type, document_id, model, content_hash, vector):
        self.saved.append((document_type, document_id, model))
        self._hashes[document_id] = content_hash

    async def get_vector(self, document_type, document_id, model):
        return [1.0, 0.0]

    async def search(self, model, query_vector, limit):
        return self._candidates[:limit]


class _FakeJobs:
    def __init__(self, jobs: dict[uuid.UUID, NormalizedJob]):
        self._jobs = jobs

    async def list_all_canonical_job_ids(self):
        return list(self._jobs)

    async def list_normalized_jobs_for_canonical(self, canonical_job_ids):
        return {job_id: self._jobs[job_id] for job_id in canonical_job_ids if job_id in self._jobs}


class _FakeMatches:
    def __init__(self) -> None:
        self.written: list = []

    async def upsert_many(self, matches):
        self.written = matches
        return len(matches)


def _service(
    jobs: dict[uuid.UUID, NormalizedJob],
    candidates: list[Candidate],
    voyage: _FakeVoyage,
    cv_text: str | None = "15 years of Python.",
    preferences: UserPreference | None = None,
    config=DEFAULTS,
) -> tuple[MatchingService, _FakeMatches]:
    matches = _FakeMatches()
    service = MatchingService(
        config,
        voyage,  # type: ignore[arg-type]
        _FakeCandidates(cv_text, preferences),  # type: ignore[arg-type]
        _FakeJobs(jobs),  # type: ignore[arg-type]
        _FakeEmbeddings(candidates),  # type: ignore[arg-type]
        matches,  # type: ignore[arg-type]
    )
    return service, matches


@pytest.mark.asyncio
async def test_a_user_without_a_cv_is_skipped_with_a_reason() -> None:
    """Producing zero matches and producing zero matches *because there is
    nothing to match against* are different outcomes, and the System page shows
    the difference."""
    service, matches = _service({}, [], _FakeVoyage(), cv_text=None)

    result = await service.run_for_user(_USER_ID)

    assert result.ran is False
    assert result.skipped_reason == "no CV uploaded"
    assert matches.written == []


@pytest.mark.asyncio
async def test_an_empty_cv_is_skipped_rather_than_embedded() -> None:
    service, _ = _service({}, [], _FakeVoyage(), cv_text="   \n  ")

    result = await service.run_for_user(_USER_ID)

    assert "no readable text" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_an_unembedded_corpus_says_so_instead_of_returning_nothing() -> None:
    service, _ = _service({}, [], _FakeVoyage())

    result = await service.run_for_user(_USER_ID)

    assert "no vacancies are embedded" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_score_blends_similarity_and_rerank_relevance() -> None:
    job_id = uuid.uuid4()
    voyage = _FakeVoyage(relevance={"Backend Engineer": 0.9})
    service, matches = _service(
        {job_id: _job()}, [Candidate(document_id=job_id, similarity=0.5)], voyage
    )

    result = await service.run_for_user(_USER_ID)

    match = matches.written[0]
    assert match.similarity == 0.5
    assert match.relevance == 0.9
    # 0.5*0.3 + 0.9*0.7 = 0.78
    assert match.score == 78.0
    assert match.recommendation is Recommendation.APPLY
    assert match.embedding_model == "voyage-test"
    assert match.rerank_model == "rerank-test"
    assert result.reranked == 1


@pytest.mark.asyncio
async def test_filtered_out_jobs_are_stored_with_the_rule_they_broke() -> None:
    """A vacancy missing from the list because of the user's own rule has to be
    explainable — otherwise "where did that job go" has no answer anywhere."""
    job_id = uuid.uuid4()
    service, matches = _service(
        {job_id: _job(company="Acme")},
        [Candidate(document_id=job_id, similarity=0.9)],
        _FakeVoyage(),
        preferences=UserPreference(user_id=str(_USER_ID), companies_blacklist=["Acme"]),
    )

    result = await service.run_for_user(_USER_ID)

    match = matches.written[0]
    assert match.eligible is False
    assert match.recommendation is Recommendation.SKIP
    assert 'company "Acme" is blacklisted' in match.filter_reasons
    assert result.eligible == 0
    assert result.filtered_out == 1


@pytest.mark.asyncio
async def test_filtered_out_jobs_never_reach_the_reranker() -> None:
    """Reranking is the one part of a run that costs per document, so nothing the
    user already ruled out should ever be paid for."""
    kept, blocked = uuid.uuid4(), uuid.uuid4()
    voyage = _FakeVoyage()
    service, _ = _service(
        {kept: _job(title="Backend Engineer"), blocked: _job(title="PHP Developer", company="Bad")},
        [
            Candidate(document_id=blocked, similarity=0.95),
            Candidate(document_id=kept, similarity=0.9),
        ],
        voyage,
        preferences=UserPreference(user_id=str(_USER_ID), companies_blacklist=["Bad"]),
    )

    await service.run_for_user(_USER_ID)

    assert [_title_of(document) for document in voyage.rerank_calls[0]] == ["Backend Engineer"]


@pytest.mark.asyncio
async def test_only_the_top_k_are_reranked_and_the_rest_keep_similarity_only() -> None:
    job_ids = [uuid.uuid4() for _ in range(3)]
    jobs = {job_id: _job(title=f"Job {index}") for index, job_id in enumerate(job_ids)}
    candidates = [
        Candidate(document_id=job_id, similarity=0.9 - index * 0.1)
        for index, job_id in enumerate(job_ids)
    ]
    service, matches = _service(
        jobs, candidates, _FakeVoyage(), config=replace(DEFAULTS, rerank_top_k=1)
    )

    result = await service.run_for_user(_USER_ID)

    reranked = [match for match in matches.written if match.relevance is not None]
    plain = [match for match in matches.written if match.relevance is None]
    assert len(reranked) == 1
    assert len(plain) == 2
    assert result.reranked == 1
    # Not reranked means scored on similarity alone, and labelled as such.
    assert plain[0].rerank_model is None


@pytest.mark.asyncio
async def test_a_rerank_failure_degrades_to_similarity_and_says_so() -> None:
    job_id = uuid.uuid4()
    service, matches = _service(
        {job_id: _job()},
        [Candidate(document_id=job_id, similarity=0.5)],
        _FakeVoyage(rerank_fails=True),
    )

    result = await service.run_for_user(_USER_ID)

    assert result.rerank_failed is True
    assert matches.written[0].relevance is None
    assert matches.written[0].score == 50.0


@pytest.mark.asyncio
async def test_rerank_position_records_where_the_reranker_put_each_job() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    voyage = _FakeVoyage(relevance={"Low similarity": 0.99, "High similarity": 0.1})
    service, matches = _service(
        {first: _job(title="High similarity"), second: _job(title="Low similarity")},
        [
            Candidate(document_id=first, similarity=0.9),
            Candidate(document_id=second, similarity=0.2),
        ],
        voyage,
    )

    await service.run_for_user(_USER_ID)

    by_id = {match.canonical_job_id: match for match in matches.written}
    assert by_id[str(second)].rerank_position == 1
    assert by_id[str(first)].rerank_position == 2
