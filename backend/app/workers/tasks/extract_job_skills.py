"""Runs LLM skill extraction once per canonical job (see
app/services/job_skill_extraction_service.py), then fans out scoring for every user
who's finished onboarding — inserted between scrape.fetch_source and
score.score_job_for_user so the LLM call happens once per job, not once per
(job x user). Safe to re-run: extraction overwrites the same job_source_records row,
and score_job_for_user is itself idempotent.

Uses build_job_llm_provider (Groq first, small local Ollama model on rate limit)
— this runs on every newly-scraped job, the same high-volume job-pipeline call
site as AI matching, so it deliberately never touches Gemini (reserved for CV
analysis and preferences AI-fill — see docs/matching-engine.md).
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


async def _run(canonical_job_id: str, user_ids: list[str]) -> None:
    settings = await get_effective_settings(get_settings())
    async with session_scope() as session:
        service = JobSkillExtractionService(
            JobRepository(session), build_job_llm_provider(settings)
        )
        await service.extract_and_save(uuid.UUID(canonical_job_id))

    for user_id in user_ids:
        score_job_for_user.delay(user_id, canonical_job_id)


@celery_app.task(name="extract.extract_job_skills")
def extract_job_skills(canonical_job_id: str, user_ids: list[str]) -> dict[str, str]:
    asyncio.run(_run(canonical_job_id, user_ids))
    return {"canonical_job_id": canonical_job_id}
