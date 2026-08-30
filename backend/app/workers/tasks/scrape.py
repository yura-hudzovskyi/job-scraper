"""Each tick scrapes ONE category for one source — whichever has gone longest
without a run (see JobRepository.get_least_recently_scraped_category) — capped at
Settings.scrape_max_jobs_per_run listings, rather than the old fixed empty-keyword
"whatever's in the default feed" approach. Over many ticks this rotates through
every category in app/integrations/sources/categories.py, so categories DOU/Djinni
support beyond generic software-engineering roles (Artist, Design, Unity, ...)
actually get scraped instead of being permanently invisible to the app.

A failed run is still recorded (errors=1) so a persistently-broken category can't
monopolize "next up" forever — pick_next_category always prefers a truly
never-attempted category, but among already-attempted ones it goes by longest ago,
successful or not.

Enqueues skill extraction for each newly discovered job — which itself fans out
scoring to every user who's finished onboarding once extraction completes (see
workers/tasks/extract_job_skills.py). Safe to re-run — JobIngestionService skips
already-known external ids before any detail fetch, and both extraction and
score_job_for_user are themselves idempotent. See docs/source-adapters.md.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.config.settings import get_settings
from app.db.session import session_scope
from app.integrations.sources.base import JobSearchCriteria
from app.integrations.sources.categories import CATEGORIES_BY_SOURCE
from app.integrations.sources.registry import build_default_registry
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.job_repository import JobRepository
from app.services.job_ingestion_service import IngestionResult, JobIngestionService
from app.workers.celery_app import celery_app
from app.workers.tasks.extract_job_skills import extract_job_skills


async def _run(source_name: str) -> IngestionResult:
    settings = get_settings()
    categories = CATEGORIES_BY_SOURCE[source_name]
    adapter = build_default_registry().get(source_name)
    started_at = datetime.now(UTC)

    async with session_scope() as session:
        category = await JobRepository(session).get_least_recently_scraped_category(
            source_name, categories
        )

    result = IngestionResult(jobs_seen=0, jobs_processed=0, processed_canonical_job_ids=[])
    user_ids: list[uuid.UUID] = []
    errors = 0
    try:
        async with session_scope() as session:
            ingestion = JobIngestionService(JobRepository(session))
            result = await ingestion.ingest_source(
                adapter,
                JobSearchCriteria(keywords=[category]),
                max_jobs=settings.scrape_max_jobs_per_run,
            )
            user_ids = await CandidateRepository(session).list_user_ids_with_profile()
    except Exception:
        errors = 1
        raise
    finally:
        async with session_scope() as session:
            await JobRepository(session).record_scrape_run(
                source=source_name,
                category=category,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                jobs_seen=result.jobs_seen,
                new_count=result.jobs_processed,
                errors=errors,
            )

    user_id_strings = [str(user_id) for user_id in user_ids]
    for canonical_job_id in result.processed_canonical_job_ids:
        extract_job_skills.delay(canonical_job_id, user_id_strings)

    return result


@celery_app.task(name="scrape.fetch_source")
def fetch_source(source_name: str) -> dict[str, int]:
    result = asyncio.run(_run(source_name))
    return {"jobs_seen": result.jobs_seen, "jobs_processed": result.jobs_processed}
