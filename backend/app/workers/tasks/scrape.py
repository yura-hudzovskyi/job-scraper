"""Fetches new jobs from one source, normalizes and dedups them, persists them, and
enqueues scoring for each newly discovered job for every user who's finished
onboarding (has an analyzed CandidateProfile — score_job_for_user hard-requires one).
Safe to re-run — JobIngestionService skips already-known external ids before any
detail fetch, and score_job_for_user is itself idempotent (upsert on
(user_id, canonical_job_id)). See docs/source-adapters.md.
"""

import asyncio

from app.db.session import session_scope
from app.integrations.sources.base import JobSearchCriteria
from app.integrations.sources.registry import build_default_registry
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.job_repository import JobRepository
from app.services.job_ingestion_service import IngestionResult, JobIngestionService
from app.workers.celery_app import celery_app
from app.workers.tasks.score import score_job_for_user


async def _run(source_name: str, keywords: list[str]) -> IngestionResult:
    adapter = build_default_registry().get(source_name)
    async with session_scope() as session:
        ingestion = JobIngestionService(JobRepository(session))
        result = await ingestion.ingest_source(adapter, JobSearchCriteria(keywords=keywords))
        user_ids = await CandidateRepository(session).list_user_ids_with_profile()

    for canonical_job_id in result.processed_canonical_job_ids:
        for user_id in user_ids:
            score_job_for_user.delay(str(user_id), canonical_job_id)

    return result


@celery_app.task(name="scrape.fetch_source")
def fetch_source(source_name: str, keywords: list[str] | None = None) -> dict[str, int]:
    result = asyncio.run(_run(source_name, keywords or []))
    return {"jobs_seen": result.jobs_seen, "jobs_processed": result.jobs_processed}
