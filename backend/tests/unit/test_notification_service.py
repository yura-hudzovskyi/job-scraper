import uuid
from datetime import UTC, datetime

import pytest

from app.domain.matching.models import JobMatch, Recommendation
from app.domain.notifications.models import JobMatchNotification
from app.domain.notifications.policy import NotificationPolicy
from app.services.notification_service import NotificationService


class _FakeProvider:
    def __init__(self, fail: bool = False):
        self.sent: list[JobMatchNotification] = []
        self._fail = fail

    async def send_job_match(self, notification: JobMatchNotification) -> None:
        if self._fail:
            raise RuntimeError("telegram down")
        self.sent.append(notification)


class _FakeRepository:
    def __init__(self) -> None:
        self._notifications: dict[tuple, uuid.UUID] = {}
        self._delivered: set[tuple] = set()
        self.recorded_errors: list[str | None] = []

    async def get_or_create_notification(self, user_id, job_match_id, channel):
        key = (user_id, job_match_id, channel)
        if key not in self._notifications:
            self._notifications[key] = uuid.uuid4()
        return self._notifications[key]

    async def has_successful_delivery(self, notification_id, channel):
        return (notification_id, channel) in self._delivered

    async def record_delivery(self, notification_id, channel, error=None):
        self.recorded_errors.append(error)
        if error is None:
            self._delivered.add((notification_id, channel))


def _notification(score: float = 90.0) -> JobMatchNotification:
    match = JobMatch(
        id=str(uuid.uuid4()),
        user_id="u1",
        canonical_job_id="c1",
        eligible=True,
        score=score,
        similarity=0.7,
        relevance=score / 100,
        recommendation=Recommendation.APPLY,
    )
    return JobMatchNotification(
        match=match,
        job_title="Backend Engineer",
        company="Acme",
        source_links=[("dou", "https://x/1")],
    )


@pytest.mark.asyncio
async def test_sends_when_the_policy_allows_it() -> None:
    provider = _FakeProvider()
    service = NotificationService(NotificationPolicy(), provider, _FakeRepository())  # type: ignore[arg-type]

    sent = await service.notify_if_relevant(
        uuid.uuid4(), _notification(90.0), now=datetime(2026, 1, 1, 14, tzinfo=UTC)
    )

    assert sent is True
    assert len(provider.sent) == 1


@pytest.mark.asyncio
async def test_does_not_send_below_threshold() -> None:
    provider = _FakeProvider()
    service = NotificationService(NotificationPolicy(), provider, _FakeRepository())  # type: ignore[arg-type]

    sent = await service.notify_if_relevant(
        uuid.uuid4(), _notification(50.0), now=datetime(2026, 1, 1, 14, tzinfo=UTC)
    )

    assert sent is False
    assert provider.sent == []


@pytest.mark.asyncio
async def test_does_not_send_during_quiet_hours() -> None:
    provider = _FakeProvider()
    service = NotificationService(NotificationPolicy(), provider, _FakeRepository())  # type: ignore[arg-type]

    sent = await service.notify_if_relevant(
        uuid.uuid4(), _notification(90.0), now=datetime(2026, 1, 1, 23, tzinfo=UTC)
    )

    assert sent is False
    assert provider.sent == []


@pytest.mark.asyncio
async def test_does_not_send_twice_for_the_same_match() -> None:
    provider = _FakeProvider()
    repository = _FakeRepository()  # type: ignore[assignment]
    service = NotificationService(NotificationPolicy(), provider, repository)  # type: ignore[arg-type]
    notification = _notification(90.0)
    user_id = uuid.uuid4()

    await service.notify_if_relevant(user_id, notification, now=datetime(2026, 1, 1, 14, tzinfo=UTC))
    await service.notify_if_relevant(user_id, notification, now=datetime(2026, 1, 1, 14, tzinfo=UTC))

    assert len(provider.sent) == 1


@pytest.mark.asyncio
async def test_records_error_and_reraises_on_delivery_failure() -> None:
    provider = _FakeProvider(fail=True)
    repository = _FakeRepository()  # type: ignore[assignment]
    service = NotificationService(NotificationPolicy(), provider, repository)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        await service.notify_if_relevant(
            uuid.uuid4(), _notification(90.0), now=datetime(2026, 1, 1, 14, tzinfo=UTC)
        )

    assert repository.recorded_errors == ["telegram down"]
