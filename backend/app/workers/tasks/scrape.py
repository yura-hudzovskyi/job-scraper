"""Fetches new jobs from one source, normalizes and dedups them, persists them, and
enqueues skill extraction for each newly discovered job — which itself fans out
scoring to every user who's finished onboarding once extraction completes (see
workers/tasks/extract_job_skills.py). Safe to re-run — JobIngestionService skips
already-known external ids before any detail fetch, and both extraction and
score_job_for_user are themselves idempotent. See docs/source-adapters.md.
"""

import asyncio

from app.db.session import session_scope
from app.integrations.sources.base import JobSearchCriteria
from app.integrations.sources.registry import build_default_registry
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.job_repository import JobRepository
from app.services.job_ingestion_service import IngestionResult, JobIngestionService
from app.workers.celery_app import celery_app
from app.workers.tasks.extract_job_skills import extract_job_skills


async def _run(source_name: str, keywords: list[str]) -> IngestionResult:
    adapter = build_default_registry().get(source_name)
    async with session_scope() as session:
        ingestion = JobIngestionService(JobRepository(session))
        result = await ingestion.ingest_source(adapter, JobSearchCriteria(keywords=keywords))
        user_ids = await CandidateRepository(session).list_user_ids_with_profile()

    user_id_strings = [str(user_id) for user_id in user_ids]
    for canonical_job_id in result.processed_canonical_job_ids:
        extract_job_skills.delay(canonical_job_id, user_id_strings)

    return result


@celery_app.task(name="scrape.fetch_source")
def fetch_source(source_name: str, keywords: list[str] | None = None) -> dict[str, int]:
    result = asyncio.run(_run(source_name, keywords or []))
    return {"jobs_seen": result.jobs_seen, "jobs_processed": result.jobs_processed}
