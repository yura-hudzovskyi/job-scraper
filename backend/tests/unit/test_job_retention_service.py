import uuid
from datetime import datetime

import pytest

from app.services.job_retention_service import JobRetentionService


class _FakeJobRepository:
    def __init__(self, stale_ids: list[uuid.UUID]):
        self._stale_ids = stale_ids
        self.deleted_for: list[uuid.UUID] | None = None

    async def find_stale_canonical_job_ids(self, cutoff: datetime) -> list[uuid.UUID]:
        return self._stale_ids

    async def delete_stale_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        self.deleted_for = canonical_job_ids


class _FakeMatchRepository:
    def __init__(self, job_match_ids: list[uuid.UUID]):
        self._job_match_ids = job_match_ids
        self.deleted_for: list[uuid.UUID] | None = None

    async def find_ids_for_canonical_jobs(self, canonical_job_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        return self._job_match_ids

    async def delete_for_canonical_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        self.deleted_for = canonical_job_ids


class _FakeNotificationRepository:
    def __init__(self) -> None:
        self.deleted_for: list[uuid.UUID] | None = None

    async def delete_for_job_matches(self, job_match_ids: list[uuid.UUID]) -> None:
        self.deleted_for = job_match_ids


@pytest.mark.asyncio
async def test_purge_stale_jobs_deletes_in_dependency_order() -> None:
    canonical_job_id = uuid.uuid4()
    job_match_id = uuid.uuid4()
    job_repository = _FakeJobRepository([canonical_job_id])
    match_repository = _FakeMatchRepository([job_match_id])
    notification_repository = _FakeNotificationRepository()

    service = JobRetentionService(job_repository, match_repository, notification_repository)  # type: ignore[arg-type]
    purged = await service.purge_stale_jobs(retention_days=18)

    assert purged == 1
    assert notification_repository.deleted_for == [job_match_id]
    assert match_repository.deleted_for == [canonical_job_id]
    assert job_repository.deleted_for == [canonical_job_id]


@pytest.mark.asyncio
async def test_purge_stale_jobs_is_a_clean_no_op_when_nothing_is_stale() -> None:
    job_repository = _FakeJobRepository([])
    match_repository = _FakeMatchRepository([])
    notification_repository = _FakeNotificationRepository()

    service = JobRetentionService(job_repository, match_repository, notification_repository)  # type: ignore[arg-type]
    purged = await service.purge_stale_jobs(retention_days=18)

    assert purged == 0
    assert notification_repository.deleted_for is None
    assert match_repository.deleted_for is None
    assert job_repository.deleted_for is None
