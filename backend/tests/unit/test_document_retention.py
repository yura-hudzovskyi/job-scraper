"""Deletion order around the new revision tables.

Nothing in this schema sets `ondelete=`, so a revision that outlives the
`job_source_records` row it points at is a foreign key violation, not a leak.
These tests pin the order the services delete in, because the order is the part
that can be wrong and it fails only against a real database.
"""

import uuid
from datetime import datetime

import pytest

from app.domain.documents.models import EntityKind
from app.services.cv_service import CvService
from app.services.job_retention_service import JobRetentionService


class _FakeJobRepository:
    def __init__(self, stale_ids: list[uuid.UUID], source_record_ids: list[uuid.UUID]):
        self._stale_ids = stale_ids
        self._source_record_ids = source_record_ids
        self.deleted_for: list[uuid.UUID] | None = None
        self.calls: list[str] = []

    async def find_stale_canonical_job_ids(self, cutoff: datetime) -> list[uuid.UUID]:
        return self._stale_ids

    async def find_source_record_ids(
        self, canonical_job_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        self.calls.append("find_source_record_ids")
        return self._source_record_ids

    async def delete_stale_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        self.calls.append("delete_stale_jobs")
        self.deleted_for = canonical_job_ids


class _FakeMatchRepository:
    def __init__(self, job_match_ids: list[uuid.UUID]):
        self._job_match_ids = job_match_ids

    async def find_ids_for_canonical_jobs(
        self, canonical_job_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        return self._job_match_ids

    async def delete_for_canonical_jobs(self, canonical_job_ids: list[uuid.UUID]) -> None:
        return None


class _FakeNotificationRepository:
    async def delete_for_job_matches(self, job_match_ids: list[uuid.UUID]) -> None:
        return None


class _FakeDocumentRepository:
    def __init__(self) -> None:
        self.deleted: list[tuple[EntityKind, list[uuid.UUID]]] = []
        self.order: list[str] = []

    async def delete_for_owners(
        self, entity_kind: EntityKind, owner_ids: list[uuid.UUID]
    ) -> dict[str, int]:
        self.order.append("delete_revisions")
        self.deleted.append((entity_kind, owner_ids))
        return {"document_revisions": len(owner_ids)}


@pytest.mark.asyncio
async def test_purge_deletes_revisions_before_the_records_they_point_at() -> None:
    canonical_job_id = uuid.uuid4()
    source_record_id = uuid.uuid4()
    jobs = _FakeJobRepository([canonical_job_id], [source_record_id])
    documents = _FakeDocumentRepository()

    service = JobRetentionService(
        jobs,  # type: ignore[arg-type]
        _FakeMatchRepository([uuid.uuid4()]),  # type: ignore[arg-type]
        _FakeNotificationRepository(),  # type: ignore[arg-type]
        None,
        documents,  # type: ignore[arg-type]
    )
    purged = await service.purge_stale_jobs(retention_days=18)

    assert purged == 1
    assert documents.deleted == [(EntityKind.JOB, [source_record_id])]
    # The revisions must be gone before delete_stale_jobs removes their owner.
    assert jobs.calls == ["find_source_record_ids", "delete_stale_jobs"]


@pytest.mark.asyncio
async def test_purge_still_works_without_a_document_repository() -> None:
    """The argument is optional so the existing call sites and their tests keep
    working — a deployment that has not run the Phase 1 migration yet has no
    revisions to delete."""
    canonical_job_id = uuid.uuid4()
    jobs = _FakeJobRepository([canonical_job_id], [])

    service = JobRetentionService(
        jobs,  # type: ignore[arg-type]
        _FakeMatchRepository([]),  # type: ignore[arg-type]
        _FakeNotificationRepository(),  # type: ignore[arg-type]
    )

    assert await service.purge_stale_jobs(retention_days=18) == 1
    assert jobs.calls == ["delete_stale_jobs"]


@pytest.mark.asyncio
async def test_purge_touches_nothing_when_no_job_is_stale() -> None:
    jobs = _FakeJobRepository([], [])
    documents = _FakeDocumentRepository()

    service = JobRetentionService(
        jobs,  # type: ignore[arg-type]
        _FakeMatchRepository([]),  # type: ignore[arg-type]
        _FakeNotificationRepository(),  # type: ignore[arg-type]
        None,
        documents,  # type: ignore[arg-type]
    )

    assert await service.purge_stale_jobs(retention_days=18) == 0
    assert documents.deleted == []
    assert jobs.calls == []


# --- CV deletion -------------------------------------------------------------


class _FakeCandidateRepository:
    def __init__(self, owns: bool):
        self._owns = owns
        self.deleted: uuid.UUID | None = None
        self.order: list[str] = []

    async def owns_cv_document(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        self.order.append("owns")
        return self._owns

    async def delete_cv_document(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        self.order.append("delete_cv")
        self.deleted = cv_document_id
        return True


@pytest.mark.asyncio
async def test_deleting_a_cv_clears_its_revisions_first() -> None:
    cv_id = uuid.uuid4()
    candidates = _FakeCandidateRepository(owns=True)
    documents = _FakeDocumentRepository()

    service = CvService(candidates, documents)  # type: ignore[arg-type]
    deleted = await service.delete_cv(uuid.uuid4(), cv_id)

    assert deleted is True
    assert documents.deleted == [(EntityKind.CANDIDATE, [cv_id])]
    assert candidates.order == ["owns", "delete_cv"]


@pytest.mark.asyncio
async def test_deleting_someone_elses_cv_deletes_nothing() -> None:
    """Ownership is checked before the revisions are cleared — otherwise a
    guessed id would wipe another user's revision history and then report 404."""
    candidates = _FakeCandidateRepository(owns=False)
    documents = _FakeDocumentRepository()

    service = CvService(candidates, documents)  # type: ignore[arg-type]
    deleted = await service.delete_cv(uuid.uuid4(), uuid.uuid4())

    assert deleted is False
    assert documents.deleted == []
    assert candidates.deleted is None
