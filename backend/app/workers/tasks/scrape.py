"""Fetches new jobs from one source, normalizes and dedups them, and persists them.
Safe to re-run — JobIngestionService skips already-known external ids before any
detail fetch. See docs/source-adapters.md.
"""

import asyncio

from app.db.session import session_scope
from app.integrations.sources.base import JobSearchCriteria
from app.integrations.sources.registry import build_default_registry
from app.repositories.job_repository import JobRepository
from app.services.job_ingestion_service import IngestionResult, JobIngestionService
from app.workers.celery_app import celery_app


async def _run(source_name: str, keywords: list[str]) -> IngestionResult:
    adapter = build_default_registry().get(source_name)
    async with session_scope() as session:
        ingestion = JobIngestionService(JobRepository(session))
        return await ingestion.ingest_source(adapter, JobSearchCriteria(keywords=keywords))


@celery_app.task(name="scrape.fetch_source")
def fetch_source(source_name: str, keywords: list[str] | None = None) -> dict:
    result = asyncio.run(_run(source_name, keywords or []))
    return {"jobs_seen": result.jobs_seen, "jobs_processed": result.jobs_processed}
