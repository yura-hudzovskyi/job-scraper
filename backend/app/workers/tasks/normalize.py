"""Re-normalizes an already-stored RawJob without re-fetching it from the source —
useful for reprocessing everything after a mapper bugfix. See docs/domain-model.md.
"""

import asyncio
import uuid

from app.db.session import session_scope
from app.integrations.sources.registry import build_default_registry
from app.repositories.job_repository import JobRepository
from app.services.job_ingestion_service import JobIngestionService
from app.workers.celery_app import celery_app


async def _run(raw_job_id: str) -> str:
    async with session_scope() as session:
        repository = JobRepository(session)
        raw_job = await repository.get_raw_job(uuid.UUID(raw_job_id))
        adapter = build_default_registry().get(raw_job.source)
        canonical_job_id = await JobIngestionService(repository).ingest_raw_job(adapter, raw_job)
    return str(canonical_job_id)


@celery_app.task(name="normalize.reprocess_raw_job")
def reprocess_raw_job(raw_job_id: str) -> str:
    return asyncio.run(_run(raw_job_id))
