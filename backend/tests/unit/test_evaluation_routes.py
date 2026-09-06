"""The annotation surface, and the one thing it must never do.

Spec 20.1 rules out generating judgements with a model and calling them ground
truth. This is the layer where that shortcut would be easiest to take, so what
these endpoints accept is deliberately narrow: a label comes from a person or it
does not exist.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.evaluation import (
    JudgeRequest,
    judge_pair,
    next_pair,
    unjudge_pair,
)
from app.repositories.evaluation_repository import PairToJudge

USER = uuid.uuid4()
PAIR_ID = uuid.uuid4()


CANDIDATE = uuid.uuid4()


class _FakeRepository:
    def __init__(self, pairs: list[PairToJudge] | None = None):
        self._pairs = pairs or []
        self.judged: list[tuple[uuid.UUID, int, str]] = []
        self.cleared: list[uuid.UUID] = []
        self.queried_for: list[uuid.UUID] = []

    async def next_to_judge(
        self, candidate_revision_id: uuid.UUID, limit: int = 1
    ) -> list[PairToJudge]:
        self.queried_for.append(candidate_revision_id)
        return self._pairs[:limit]

    async def judge(
        self,
        pair_id: uuid.UUID,
        label: int,
        annotator: str,
        candidate_revision_id: uuid.UUID | None = None,
    ) -> bool:
        if pair_id != PAIR_ID or candidate_revision_id not in (None, CANDIDATE):
            return False
        self.judged.append((pair_id, label, annotator))
        return True

    async def clear_judgement(
        self, pair_id: uuid.UUID, candidate_revision_id: uuid.UUID | None = None
    ) -> bool:
        if pair_id != PAIR_ID or candidate_revision_id not in (None, CANDIDATE):
            return False
        self.cleared.append(pair_id)
        return True

    async def progress(self, candidate_revision_id: uuid.UUID) -> dict[str, int]:
        return {"seed_total": 300, "seed_judged": len(self.judged)}

    async def label_distribution(self, candidate_revision_id: uuid.UUID) -> dict[int, int]:
        return {label: 1 for _, label, _ in self.judged}


class _FakeService:
    """Stands in for EvaluationService, which the routes use only to resolve
    which CV the authenticated user is evaluated against."""

    def __init__(self, candidate: uuid.UUID | None = CANDIDATE):
        self._candidate = candidate
        self.asked_for: list[uuid.UUID] = []

    async def candidate_revision(self, user_id: uuid.UUID) -> uuid.UUID | None:
        self.asked_for.append(user_id)
        return self._candidate


def _pair(score: float = 61.0) -> PairToJudge:
    return PairToJudge(
        id=str(PAIR_ID),
        canonical_job_id=str(uuid.uuid4()),
        job_title="Senior Python Developer",
        job_company="Acme",
        job_text="We need Python and PostgreSQL.",
        system_score=score,
        label=None,
        tier="seed",
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Swap the repository and the service the endpoints construct for fakes."""

    def _install(repository: _FakeRepository, service: _FakeService | None = None) -> _FakeService:
        resolved = service or _FakeService()
        monkeypatch.setattr(
            "app.api.routes.evaluation.EvaluationRepository", lambda session: repository
        )
        monkeypatch.setattr("app.api.routes.evaluation.EvaluationService", lambda session: resolved)
        return resolved

    return _install


@pytest.mark.asyncio
async def test_the_next_pair_carries_the_text_a_person_has_to_read(patched) -> None:
    repository = _FakeRepository([_pair()])
    patched(repository)

    pair = await next_pair(USER, SimpleNamespace())  # type: ignore[arg-type]

    assert pair is not None
    assert pair.job_title == "Senior Python Developer"
    assert "PostgreSQL" in pair.job_text


@pytest.mark.asyncio
async def test_an_empty_queue_is_null_rather_than_an_error(patched) -> None:
    """Either the set is fully judged or nobody sampled one yet. Both are normal
    states for a screen to render."""
    patched(_FakeRepository([]))

    assert await next_pair(USER, SimpleNamespace()) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_judgement_records_who_made_it(patched) -> None:
    """20.1 forbids letting the person who tuned a weight be the only annotator
    for the slice it affects, which is unanswerable if nobody is recorded."""
    repository = _FakeRepository([_pair()])
    patched(repository)

    await judge_pair(PAIR_ID, JudgeRequest(label=3), USER, SimpleNamespace())  # type: ignore[arg-type]

    assert repository.judged == [(PAIR_ID, 3, str(USER))]


@pytest.mark.asyncio
async def test_judging_returns_progress_so_the_screen_needs_one_call(patched) -> None:
    """A minute per judgement is the budget; a second round trip to refresh a
    counter comes out of it."""
    repository = _FakeRepository([_pair()])
    patched(repository)

    progress = await judge_pair(PAIR_ID, JudgeRequest(label=2), USER, SimpleNamespace())  # type: ignore[arg-type]

    assert progress.counts["seed_judged"] == 1
    assert progress.labels["2"].startswith("relevant")


@pytest.mark.asyncio
async def test_a_judgement_can_be_taken_back(patched) -> None:
    """A wrong label is worse than a missing one — the metric reports it with
    full confidence."""
    repository = _FakeRepository([_pair()])
    patched(repository)

    await unjudge_pair(PAIR_ID, USER, SimpleNamespace())  # type: ignore[arg-type]

    assert repository.cleared == [PAIR_ID]


@pytest.mark.asyncio
async def test_judging_a_pair_that_does_not_exist_is_a_404(patched) -> None:
    patched(_FakeRepository([]))

    with pytest.raises(HTTPException) as raised:
        await judge_pair(uuid.uuid4(), JudgeRequest(label=1), USER, SimpleNamespace())  # type: ignore[arg-type]

    assert raised.value.status_code == 404


def test_only_the_four_labels_the_scale_defines_are_accepted() -> None:
    """0-3 (spec 20.1). A 5 or a -1 would be stored, then averaged, then
    believed."""
    for label in (0, 1, 2, 3):
        assert JudgeRequest(label=label).label == label  # type: ignore[arg-type]
    for bad in (-1, 4, 10):
        with pytest.raises(ValueError):
            JudgeRequest(label=bad)  # type: ignore[arg-type]


# --- the scoping bug that cost 600 judgements --------------------------------


@pytest.mark.asyncio
async def test_the_queue_is_asked_for_this_user_s_own_candidate(patched) -> None:
    """The failure this exists to prevent: with no candidate filter the queue
    served whatever was unjudged across the whole install, so a software
    engineer spent 600 judgements on vacancies ranked for a 3D artist's CV."""
    repository = _FakeRepository([_pair()])
    service = patched(repository)

    await next_pair(USER, SimpleNamespace())  # type: ignore[arg-type]

    assert service.asked_for == [USER]
    assert repository.queried_for == [CANDIDATE]


@pytest.mark.asyncio
async def test_a_user_with_no_cv_gets_an_empty_queue_rather_than_someone_else_s(
    patched,
) -> None:
    """Nothing to evaluate against is a state to render. Falling back to the
    newest CV on the install is what produced the wrong set in the first place."""
    patched(_FakeRepository([_pair()]), _FakeService(candidate=None))

    assert await next_pair(USER, SimpleNamespace()) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_judging_a_pair_from_another_candidate_s_set_is_refused(patched) -> None:
    """A pair id is not authorisation. Without this check, an id from anywhere
    is enough to write a label into somebody else's evaluation set."""
    repository = _FakeRepository([_pair()])
    patched(repository, _FakeService(candidate=uuid.uuid4()))

    with pytest.raises(HTTPException) as raised:
        await judge_pair(PAIR_ID, JudgeRequest(label=3), USER, SimpleNamespace())  # type: ignore[arg-type]

    assert raised.value.status_code == 404
    assert repository.judged == []
