"""Scores one canonical job against a user's candidate profile + preferences and
persists the JobMatch, then enqueues notification dispatch for it — the pipeline
described in ARCHITECTURE.md ends with NotificationPolicy, not with a saved score.
"""

import asyncio
import uuid

from app.config.runtime_settings import get_effective_settings
from app.config.settings import get_settings
from app.db.session import session_scope
from app.domain.candidates.models import UserPreference
from app.domain.matching.factory import build_matching_service
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.workers.celery_app import celery_app
from app.workers.tasks.notify import dispatch_match


class ScoringUnavailable(RuntimeError):
    pass


async def _run(user_id: str, canonical_job_id: str) -> dict[str, float | str]:
    settings = await get_effective_settings(get_settings())
    matching_service = build_matching_service(settings)
    if matching_service is None:
        raise ScoringUnavailable("no embedding provider configured")

    async with session_scope() as session:
        job_repository = JobRepository(session)
        candidate_repository = CandidateRepository(session)
        match_repository = MatchRepository(session)

        job = await job_repository.get_normalized_job_for_canonical(uuid.UUID(canonical_job_id))
        if job is None:
            raise LookupError(f"canonical job {canonical_job_id} has no normalized source record")

        profile = await candidate_repository.get_latest_candidate_profile(uuid.UUID(user_id))
        if profile is None:
            raise LookupError(f"user {user_id} has no analyzed CandidateProfile yet")

        preferences = await candidate_repository.get_preferences(
            uuid.UUID(user_id)
        ) or UserPreference(user_id=user_id, desired_salary_usd=None)

        job_version = await job_repository.refresh_canonical_content_version(
            uuid.UUID(canonical_job_id)
        )

        # Whatever the retrieval/rerank pass concluded about this vacancy, if it
        # has run — see app/workers/tasks/retrieve.py.
        existing = await match_repository.get_for_canonical_job(
            uuid.UUID(user_id), uuid.UUID(canonical_job_id)
        )
        match = await matching_service.evaluate(
            canonical_job_id,
            job,
            profile,
            preferences,
            job_version,
            rerank_relevance=existing.relevance if existing else None,
            rerank_model=existing.relevance_model if existing else None,
        )
        if not settings.matching_pipeline_v3:
            # Pre-v3: every CONSIDER+APPLY match asks an LLM here, in scrape
            # order. Under v3 that call moves to the scheduler
            # (app/workers/tasks/enrich.py), which spends the same budget on the
            # matches where an opinion actually changes something.
            match = await matching_service.should_i_apply(job, profile, match)
        saved = await match_repository.upsert(match)

    return {"match_id": saved.id, "practical_fit": saved.practical_fit}


@celery_app.task(name="score.score_job_for_user")
def score_job_for_user(user_id: str, canonical_job_id: str) -> dict[str, float | str]:
    result = asyncio.run(_run(user_id, canonical_job_id))
    dispatch_match.delay(user_id, canonical_job_id)
    return result
