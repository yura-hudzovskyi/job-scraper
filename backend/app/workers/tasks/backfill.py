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
from datetime import timedelta

from app.config.runtime_settings import get_effective_settings
from app.config.settings import get_settings
from app.db.session import session_scope
from app.integrations.ai.llm.factory import build_llm_router
from app.integrations.ai.routing.router import Capability
from app.repositories.job_repository import JobRepository
from app.services.job_skill_extraction_service import JobSkillExtractionService
from app.workers.celery_app import celery_app
from app.workers.pacing import retry_countdown
from app.workers.tasks.embed import index_profile_embeddings
from app.workers.tasks.score import score_job_for_user

_MAX_CAPACITY_RETRIES = 3


async def _run(user_id: str) -> int:
    settings = await get_effective_settings(get_settings())
    async with session_scope() as session:
        canonical_job_ids = await JobRepository(session).list_all_canonical_job_ids()

    if settings.multi_embedding_lanes:
        # This runs exactly when the profile changed (a new CV analysis or a skill
        # correction), which is also when its section vectors are stale.
        index_profile_embeddings.delay(user_id)

    for canonical_job_id in canonical_job_ids:
        score_job_for_user.delay(user_id, str(canonical_job_id))

    return len(canonical_job_ids)


@celery_app.task(name="backfill.score_existing_jobs_for_user")
def score_existing_jobs_for_user(user_id: str) -> dict[str, int]:
    count = asyncio.run(_run(user_id))
    return {"jobs_queued": count}


async def _run_reextract_and_rescore(user_id: str, canonical_job_id: str) -> timedelta | None:
    """Re-extracts this job's skills through the same job-pipeline provider
    (the JOB_EXTRACTION capability — see routing/policy.py) that the
    automatic per-scrape extraction already uses, then rescores. force=True: this
    is the explicit "refresh everything" action, so it re-reads the posting even
    when nothing about it changed — that is the point of pressing the button.
    Skips straight to scoring (degrading to whatever skills were already stored)
    if no job provider is configured at all."""
    settings = await get_effective_settings(get_settings())
    async with session_scope() as session:
        job_repository = JobRepository(session)
        extraction_service = JobSkillExtractionService(
            job_repository, build_llm_router(Capability.JOB_EXTRACTION, settings)
        )
        outcome = await extraction_service.extract_and_save(
            uuid.UUID(canonical_job_id), force=True
        )

    score_job_for_user.delay(user_id, canonical_job_id)
    return outcome.retry_after


@celery_app.task(
    name="backfill.reextract_and_rescore_job", bind=True, max_retries=_MAX_CAPACITY_RETRIES
)
def reextract_and_rescore_job(self, user_id: str, canonical_job_id: str) -> dict[str, str]:
    """A bulk refresh will out-run any free tier, so this comes back when the
    provider does rather than settling for the rules extraction (see
    app/workers/pacing.py)."""
    retry_after = asyncio.run(_run_reextract_and_rescore(user_id, canonical_job_id))
    if retry_after is not None and self.request.retries < _MAX_CAPACITY_RETRIES:
        raise self.retry(countdown=retry_countdown(retry_after, self.request.retries))
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
