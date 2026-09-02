"""Deletes jobs (and their matches/notifications) older than
Settings.job_retention_days, once daily. See app/services/job_retention_service.py
for the actual cross-table delete ordering.
"""

import asyncio

from app.config.settings import get_settings
from app.db.session import session_scope
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.job_retention_service import JobRetentionService
from app.workers.celery_app import celery_app


async def _run() -> int:
    settings = get_settings()
    async with session_scope() as session:
        service = JobRetentionService(
            JobRepository(session),
            MatchRepository(session),
            NotificationRepository(session),
            EmbeddingRepository(session),
        )
        return await service.purge_stale_jobs(settings.job_retention_days)


@celery_app.task(name="retention.purge_stale_jobs")
def purge_stale_jobs() -> dict[str, int]:
    purged = asyncio.run(_run())
    return {"purged": purged}
