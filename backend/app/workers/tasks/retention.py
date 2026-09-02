"""Deletes vacancies (and their matches, notifications and vectors) once they
stop appearing in scrapes. Runs daily; the window comes from the pipeline config,
so it is editable from the System page like everything else.
"""

import asyncio

from app.db.session import session_scope
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.pipeline_config_repository import PipelineConfigRepository
from app.services.job_retention_service import JobRetentionService
from app.workers.celery_app import celery_app


async def _run() -> int:
    async with session_scope() as session:
        config = await PipelineConfigRepository(session).get()
        service = JobRetentionService(
            JobRepository(session),
            MatchRepository(session),
            NotificationRepository(session),
            EmbeddingRepository(session),
        )
        return await service.purge_stale_jobs(config.job_retention_days)


@celery_app.task(name="retention.purge_stale_jobs")
def purge_stale_jobs() -> dict[str, int]:
    return {"purged": asyncio.run(_run())}
