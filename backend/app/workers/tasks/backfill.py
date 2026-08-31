"""Fans out scoring for one user across every existing canonical job — run once,
right after a CV is analyzed (see app/api/routes/cv.py::analyze_cv). Without this,
a newly-onboarded user only ever gets matches for jobs scraped *after* they
finished onboarding (extract_job_skills.py's fan-out only reaches users who were
already onboarded when a job was first scored) — their jobs list would otherwise
show the entire existing backlog, unscored, forever. Safe to re-run: score_job_for_user
is itself idempotent (upserts).
"""

import asyncio

from app.db.session import session_scope
from app.repositories.job_repository import JobRepository
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


async def _run_with_model(user_id: str, llm_model: str | None) -> int:
    async with session_scope() as session:
        canonical_job_ids = await JobRepository(session).list_all_canonical_job_ids()

    for canonical_job_id in canonical_job_ids:
        score_job_for_user.delay(user_id, str(canonical_job_id), llm_model)

    return len(canonical_job_ids)


@celery_app.task(name="backfill.rescore_all_jobs")
def rescore_all_jobs(user_id: str, llm_model: str | None = None) -> dict[str, int]:
    """Explicit, user-initiated rescore of every canonical job for a user — see
    POST /api/jobs/rescore-all. Unlike score_existing_jobs_for_user above (fired
    automatically once right after a CV is analyzed), this is triggered manually
    from the Jobs page, primarily to re-run the whole existing backlog against a
    different LLM model (llm_model overrides Settings.llm_model for this run only —
    see app/domain/matching/factory.py::build_matching_service) without changing
    the server's configured default."""
    count = asyncio.run(_run_with_model(user_id, llm_model))
    return {"jobs_queued": count}
