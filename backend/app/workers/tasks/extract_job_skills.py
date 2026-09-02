"""Runs LLM skill extraction once per canonical job (see
app/services/job_skill_extraction_service.py), then fans out scoring for every user
who's finished onboarding — inserted between scrape.fetch_source and
score.score_job_for_user so the LLM call happens once per job, not once per
(job x user). Safe to re-run: extraction overwrites the same job_source_records row,
and score_job_for_user is itself idempotent.

Runs on the JOB_EXTRACTION capability (Groq first, Gemini on rate limit)
— this runs on every newly-scraped job, the same high-volume job-pipeline call
site as AI matching, so it deliberately never touches Gemini (reserved for CV
analysis and preferences AI-fill — see docs/matching-engine.md).
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
from app.workers.tasks.embed import index_job_embeddings
from app.workers.tasks.score import score_job_for_user

# A posting whose LLM read was blocked by a rate limit is worth coming back for,
# but not forever: after this many tries the rules extraction it already has
# stands, and the job keeps its (weaker but real) requirements.
_MAX_CAPACITY_RETRIES = 3


async def _run(canonical_job_id: str, user_ids: list[str]) -> timedelta | None:
    settings = await get_effective_settings(get_settings())
    async with session_scope() as session:
        service = JobSkillExtractionService(
            JobRepository(session),
            build_llm_router(Capability.JOB_EXTRACTION, settings),
        )
        outcome = await service.extract_and_save(uuid.UUID(canonical_job_id))

    if settings.multi_embedding_lanes:
        # The posting's requirements just changed, so its section vectors have to
        # follow — see app/workers/tasks/embed.py.
        index_job_embeddings.delay(canonical_job_id)

    # Scoring runs either way: users see a result now, built on whatever
    # requirements this pass could get.
    for user_id in user_ids:
        score_job_for_user.delay(user_id, canonical_job_id)
    return outcome.retry_after


@celery_app.task(name="extract.extract_job_skills", bind=True, max_retries=_MAX_CAPACITY_RETRIES)
def extract_job_skills(self, canonical_job_id: str, user_ids: list[str]) -> dict[str, str]:
    """Reschedules itself when the posting could only be read by rules because
    the LLM had no capacity — with a countdown taken from the provider's own
    reset, so the worker slot goes back to the pool instead of waiting (see
    app/workers/pacing.py)."""
    retry_after = asyncio.run(_run(canonical_job_id, user_ids))
    if retry_after is not None and self.request.retries < _MAX_CAPACITY_RETRIES:
        raise self.retry(countdown=retry_countdown(retry_after, self.request.retries))
    return {"canonical_job_id": canonical_job_id}
