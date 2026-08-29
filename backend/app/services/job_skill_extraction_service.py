"""Use case: extract required skills from a job description via LLM, once per
canonical job (see workers/tasks/extract_job_skills.py — called at ingestion time,
not per-user, not per-match). Same shape as CvService.analyze_cv's CV-side
extraction. No LLM configured -> best-effort no-op, same "degrade gracefully"
philosophy as CV analysis, so a missing LLM doesn't fail the scrape pipeline.
"""

import uuid

from pydantic import BaseModel, Field

from app.domain.jobs.models import NormalizedJobSkill
from app.integrations.ai.llm.base import LLMProvider
from app.repositories.job_repository import JobRepository


class _ExtractedSkill(BaseModel):
    name: str
    required: bool


class _ExtractedJobSkills(BaseModel):
    skills: list[_ExtractedSkill] = Field(default_factory=list)


_EXTRACTION_PROMPT = """Extract the technical skills/technologies this job posting mentions.

- name: the skill/technology as named in the posting (e.g. "React", "PostgreSQL", \
"Kubernetes") — keep it short, don't invent skills the posting doesn't mention.
- required: true if the posting treats it as a must-have, false if it's listed as a \
nice-to-have/bonus.

Job title: {title}
Job description:
---
{description}
---
"""


class JobSkillExtractionService:
    def __init__(self, job_repository: JobRepository, llm_provider: LLMProvider | None):
        self._job_repository = job_repository
        self._llm_provider = llm_provider

    async def extract_and_save(self, canonical_job_id: uuid.UUID) -> list[NormalizedJobSkill] | None:
        if self._llm_provider is None:
            return None

        job = await self._job_repository.get_normalized_job_for_canonical(canonical_job_id)
        if job is None:
            raise LookupError(f"canonical job {canonical_job_id} has no normalized source record")

        extracted = await self._llm_provider.structured_completion(
            _EXTRACTION_PROMPT.format(title=job.title, description=job.description),
            _ExtractedJobSkills,
        )
        skills = [
            NormalizedJobSkill(name=skill.name, required=skill.required)
            for skill in extracted.skills
        ]
        await self._job_repository.update_skills_for_canonical(canonical_job_id, skills)
        return skills
