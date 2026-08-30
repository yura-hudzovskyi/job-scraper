import uuid

import pytest

from app.services.job_service import JobService


class _FakeJobRepository:
    def __init__(self) -> None:
        self.list_calls: list[tuple[int, int, set[uuid.UUID] | None]] = []
        self.count_calls: list[set[uuid.UUID] | None] = []

    async def list_canonical_jobs(
        self, limit: int, offset: int, exclude_ids: set[uuid.UUID] | None = None
    ) -> list:
        self.list_calls.append((limit, offset, exclude_ids))
        return []

    async def count_canonical_jobs(self, exclude_ids: set[uuid.UUID] | None = None) -> int:
        self.count_calls.append(exclude_ids)
        return 0


class _FakeMatchRepository:
    def __init__(self, skipped_ids: set[uuid.UUID] | None = None) -> None:
        self._skipped_ids = skipped_ids or set()
        self.skipped_lookup_calls = 0

    async def list_skipped_canonical_job_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        self.skipped_lookup_calls += 1
        return self._skipped_ids

    async def list_for_canonical_jobs(self, user_id: uuid.UUID, canonical_job_ids: list) -> dict:
        return {}


@pytest.mark.asyncio
async def test_list_jobs_excludes_skipped_by_default() -> None:
    skipped = {uuid.uuid4()}
    job_repository = _FakeJobRepository()
    match_repository = _FakeMatchRepository(skipped_ids=skipped)
    service = JobService(job_repository, match_repository)  # type: ignore[arg-type]

    await service.list_jobs(uuid.uuid4(), limit=25, offset=0)

    assert match_repository.skipped_lookup_calls == 1
    assert job_repository.list_calls[0] == (25, 0, skipped)
    assert job_repository.count_calls[0] == skipped


@pytest.mark.asyncio
async def test_list_jobs_skips_the_lookup_when_include_skipped_is_true() -> None:
    job_repository = _FakeJobRepository()
    match_repository = _FakeMatchRepository()
    service = JobService(job_repository, match_repository)  # type: ignore[arg-type]

    await service.list_jobs(uuid.uuid4(), limit=25, offset=0, include_skipped=True)

    assert match_repository.skipped_lookup_calls == 0
    assert job_repository.list_calls[0] == (25, 0, None)
    assert job_repository.count_calls[0] is None
