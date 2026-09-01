"""Fans out scoring for one user across every existing canonical job — run once,
right after a CV is analyzed (see app/api/routes/cv.py::analyze_cv). Without this,
a newly-onboarded user only ever gets matches for jobs scraped *after* they
finished onboarding (extract_job_skills.py's fan-out only reaches users who were
already onboarded when a job was first scored) — their jobs list would otherwise
show the entire existing backlog, unscored, forever. Safe to re-run: score_job_for_user
is itself idempotent (upserts).
"""

import asyncio
import uuid

from app.config.runtime_settings import get_effective_settings
from app.config.settings import get_settings
from app.db.session import session_scope
from app.integrations.ai.llm.factory import build_job_llm_provider
from app.repositories.job_repository import JobRepository
from app.services.job_skill_extraction_service import JobSkillExtractionService
from app.workers.celery_app import celery_app
from app.workers.tasks.score import score_job_for_user


async def _run(user_id: str) -> int:
    async with session_scope() as session:
        canonical_job_ids = await JobRepository(session).list_all_canonical_job_ids()

    for canonical_job_id in canonical_job_ids:
        score_job_for_user.delay(user_id, str(canonical_job_id))

    return len(canonical_job_ids)


@celery_app.task(name="backfill.score_existing_jobs_for_user")
def score_existing_jobs_for_user(user_id: str) -> dict[str, int]:
    count = asyncio.run(_run(user_id))
    return {"jobs_queued": count}


async def _run_reextract_and_rescore(user_id: str, canonical_job_id: str) -> None:
    """Re-extracts this job's skills through the same job-pipeline provider
    (Groq first, Gemini on rate limit — see build_job_llm_provider) that the
    automatic per-scrape extraction already uses, then rescores. Skips straight
    to scoring (degrading to whatever skills were already stored) if no job
    provider is configured at all."""
    settings = await get_effective_settings(get_settings())
    async with session_scope() as session:
        job_repository = JobRepository(session)
        extraction_service = JobSkillExtractionService(
            job_repository, build_job_llm_provider(settings)
        )
        await extraction_service.extract_and_save(uuid.UUID(canonical_job_id))

    score_job_for_user.delay(user_id, canonical_job_id)


@celery_app.task(name="backfill.reextract_and_rescore_job")
def reextract_and_rescore_job(user_id: str, canonical_job_id: str) -> dict[str, str]:
    asyncio.run(_run_reextract_and_rescore(user_id, canonical_job_id))
    return {"canonical_job_id": canonical_job_id}


async def _run_reextract_and_rescore_all(user_id: str) -> int:
    async with session_scope() as session:
        canonical_job_ids = await JobRepository(session).list_all_canonical_job_ids()

    for canonical_job_id in canonical_job_ids:
        reextract_and_rescore_job.delay(user_id, str(canonical_job_id))

    return len(canonical_job_ids)


@celery_app.task(name="backfill.rescore_all_jobs")
def rescore_all_jobs(user_id: str) -> dict[str, int]:
    """Explicit, user-initiated re-extraction + rescore of every canonical job for
    a user — see POST /api/jobs/rescore-all. Unlike score_existing_jobs_for_user
    above (fired automatically once right after a CV is analyzed, scoring only —
    skills were already extracted at ingestion time), this is triggered manually
    from the Jobs page to refresh both a job's extracted skills and its score
    against the whole existing backlog — e.g. after a bad extraction run, or to
    pick up a better model. Which models it runs on comes from the server config
    / System page (app/api/routes/ai_settings.py)."""
    count = asyncio.run(_run_reextract_and_rescore_all(user_id))
    return {"jobs_queued": count}
