"""Use case: delete jobs (and everything that references them) once they're older
than the retention window. The only place that knows the cross-table delete
ordering (notification_deliveries -> notifications -> job_matches -> document
embeddings -> applications -> job_source_records -> canonical_jobs -> orphaned
raw_jobs) — repositories stay scoped to their own tables, this coordinates them.
See app/workers/tasks/retention.py.
"""

from datetime import UTC, datetime, timedelta

from app.repositories.embedding_repository import JOB, EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository


class JobRetentionService:
    def __init__(
        self,
        job_repository: JobRepository,
        match_repository: MatchRepository,
        notification_repository: NotificationRepository,
        embedding_repository: EmbeddingRepository | None = None,
    ):
        self._job_repository = job_repository
        self._match_repository = match_repository
        self._notification_repository = notification_repository
        self._embedding_repository = embedding_repository

    async def purge_stale_jobs(self, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        canonical_job_ids = await self._job_repository.find_stale_canonical_job_ids(cutoff)
        if not canonical_job_ids:
            return 0

        job_match_ids = await self._match_repository.find_ids_for_canonical_jobs(canonical_job_ids)
        await self._notification_repository.delete_for_job_matches(job_match_ids)
        await self._match_repository.delete_for_canonical_jobs(canonical_job_ids)
        if self._embedding_repository is not None:
            # Vectors outlive nothing: left behind, they keep surfacing a vacancy
            # that no longer exists in retrieval.
            await self._embedding_repository.delete_for_documents(JOB, canonical_job_ids)
        await self._job_repository.delete_stale_jobs(canonical_job_ids)

        return len(canonical_job_ids)
