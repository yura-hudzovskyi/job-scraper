import uuid
from typing import Any

import pytest

from app.domain.matching.models import (
    JobMatch,
    MatchDecision,
    Recommendation,
    ScoreBreakdown,
)
from app.services.telegram_callback_service import TelegramCallbackService

_USER_ID = uuid.uuid4()
_CANONICAL_JOB_ID = uuid.uuid4()


def _match(decision: MatchDecision) -> JobMatch:
    return JobMatch(
        id="m1",
        user_id=str(_USER_ID),
        canonical_job_id=str(_CANONICAL_JOB_ID),
        eligible=True,
        requirement_match=80.0,
        practical_fit=80.0,
        breakdown=ScoreBreakdown(80, 80, 80, 80, 80, 80, 80, 80),
        recommendation=Recommendation.APPLY,
        decision=decision,
    )


class _FakeMatchRepository:
    def __init__(self, match: JobMatch | None):
        self._match = match
        self.set_decision_calls: list[tuple[uuid.UUID, uuid.UUID, MatchDecision]] = []

    async def set_decision(
        self, user_id: uuid.UUID, canonical_job_id: uuid.UUID, decision: MatchDecision
    ) -> JobMatch | None:
        self.set_decision_calls.append((user_id, canonical_job_id, decision))
        return self._match


class _FakeNotificationRepository:
    def __init__(self, user_id_by_chat_id: dict[str, uuid.UUID]):
        self._user_id_by_chat_id = user_id_by_chat_id

    async def get_user_id_for_chat_id(self, chat_id: str) -> uuid.UUID | None:
        return self._user_id_by_chat_id.get(chat_id)


class _FakeBotProvider:
    def __init__(self) -> None:
        self.answered: list[tuple[str, str]] = []
        self.cleared: list[tuple[int, int]] = []

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self.answered.append((callback_query_id, text))

    async def clear_reply_markup(self, chat_id: int, message_id: int) -> None:
        self.cleared.append((chat_id, message_id))


def _callback_update(
    data: str, chat_id: object = 555, message_id: object = 42
) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cbq1",
            "data": data,
            "message": {"chat": {"id": chat_id}, "message_id": message_id},
        }
    }


def _service(
    match: JobMatch | None, connected_chat_id: str | None = "555"
) -> tuple[TelegramCallbackService, _FakeMatchRepository, _FakeNotificationRepository, _FakeBotProvider]:
    match_repository = _FakeMatchRepository(match)
    notification_repository = _FakeNotificationRepository(
        {connected_chat_id: _USER_ID} if connected_chat_id else {}
    )
    bot_provider = _FakeBotProvider()
    service = TelegramCallbackService(
        match_repository, notification_repository, bot_provider  # type: ignore[arg-type]
    )
    return service, match_repository, notification_repository, bot_provider


@pytest.mark.asyncio
async def test_approve_records_the_decision_answers_and_clears_the_buttons() -> None:
    service, match_repository, _, bot_provider = _service(match=_match(MatchDecision.APPROVED))

    await service.handle_update(_callback_update(f"match:approve:{_CANONICAL_JOB_ID}"))

    assert match_repository.set_decision_calls == [
        (_USER_ID, _CANONICAL_JOB_ID, MatchDecision.APPROVED)
    ]
    assert bot_provider.answered == [("cbq1", "Approved!")]
    assert bot_provider.cleared == [(555, 42)]


@pytest.mark.asyncio
async def test_reject_records_the_decision() -> None:
    service, match_repository, _, bot_provider = _service(match=_match(MatchDecision.REJECTED))

    await service.handle_update(_callback_update(f"match:reject:{_CANONICAL_JOB_ID}"))

    assert match_repository.set_decision_calls == [
        (_USER_ID, _CANONICAL_JOB_ID, MatchDecision.REJECTED)
    ]
    assert bot_provider.answered == [("cbq1", "Rejected.")]


@pytest.mark.asyncio
async def test_unconnected_chat_id_is_told_and_nothing_is_recorded() -> None:
    service, match_repository, _, bot_provider = _service(
        match=_match(MatchDecision.APPROVED), connected_chat_id=None
    )

    await service.handle_update(_callback_update(f"match:approve:{_CANONICAL_JOB_ID}"))

    assert match_repository.set_decision_calls == []
    assert bot_provider.answered == [("cbq1", "Not connected.")]
    assert bot_provider.cleared == []


@pytest.mark.asyncio
async def test_match_not_found_is_told_and_buttons_are_left_alone() -> None:
    service, _, _, bot_provider = _service(match=None)

    await service.handle_update(_callback_update(f"match:approve:{_CANONICAL_JOB_ID}"))

    assert bot_provider.answered == [("cbq1", "Match not found.")]
    assert bot_provider.cleared == []


@pytest.mark.asyncio
async def test_unrecognized_callback_data_is_acknowledged_but_not_acted_on() -> None:
    service, match_repository, _, bot_provider = _service(match=_match(MatchDecision.APPROVED))

    await service.handle_update(_callback_update("save:c1"))

    assert match_repository.set_decision_calls == []
    assert bot_provider.answered == [("cbq1", "Unrecognized action.")]


@pytest.mark.asyncio
async def test_malformed_canonical_job_id_is_handled_gracefully() -> None:
    service, match_repository, _, bot_provider = _service(match=_match(MatchDecision.APPROVED))

    await service.handle_update(_callback_update("match:approve:not-a-uuid"))

    assert match_repository.set_decision_calls == []
    assert bot_provider.answered == [("cbq1", "Something went wrong.")]


@pytest.mark.asyncio
async def test_non_callback_updates_are_ignored() -> None:
    service, match_repository, _, bot_provider = _service(match=_match(MatchDecision.APPROVED))

    await service.handle_update({"message": {"text": "hello"}})

    assert match_repository.set_decision_calls == []
    assert bot_provider.answered == []
