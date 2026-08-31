"""Scores one canonical job against a user's candidate profile + preferences and
persists the JobMatch, then enqueues notification dispatch for it — the pipeline
described in ARCHITECTURE.md ends with NotificationPolicy, not with a saved score.
"""

import asyncio
import uuid

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


async def _run(
    user_id: str, canonical_job_id: str, llm_model: str | None = None
) -> dict[str, float | str]:
    matching_service = build_matching_service(get_settings(), llm_model)
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

        match = await matching_service.evaluate(canonical_job_id, job, profile, preferences)
        match = await matching_service.should_i_apply(job, profile, match)
        saved = await match_repository.upsert(match)

    return {"match_id": saved.id, "practical_fit": saved.practical_fit}


@celery_app.task(name="score.score_job_for_user")
def score_job_for_user(
    user_id: str, canonical_job_id: str, llm_model: str | None = None
) -> dict[str, float | str]:
    result = asyncio.run(_run(user_id, canonical_job_id, llm_model))
    dispatch_match.delay(user_id, canonical_job_id)
    return result
