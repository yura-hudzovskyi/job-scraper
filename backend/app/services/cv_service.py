"""Use case: extract text from an uploaded CV, then (optionally) turn it into a
structured CandidateProfile via an LLMProvider. See docs/matching-engine.md.
"""

import io
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Literal

from docx import Document
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.domain.candidates.models import (
    CandidateProfile,
    CandidateSkill,
    CvDocument,
    ExperienceEntry,
    SkillLevel,
    SkillOverride,
)
from app.domain.candidates.skill_overrides import apply_overrides, normalize
from app.integrations.ai.llm.base import LLMProvider
from app.repositories.candidate_repository import CandidateRepository
from app.services.ai_errors import LlmCallFailed, LlmNotConfigured

logger = logging.getLogger(__name__)


class UnsupportedCvFormat(ValueError):
    pass


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        return content.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        document = Document(io.BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    raise UnsupportedCvFormat(f"unsupported CV format: {suffix or '(none)'}")


class _ExtractedSkill(BaseModel):
    name: str
    level: Literal["aware", "commercial", "strong", "expert"]
    years: float | None = None


class _ExtractedExperience(BaseModel):
    company: str
    title: str
    start_date: str
    end_date: str | None = None
    description: str
    skills: list[str] = Field(default_factory=list)


class _ExtractedProfile(BaseModel):
    experience_years: float
    roles: list[str]
    skills: list[_ExtractedSkill]
    experience: list[_ExtractedExperience] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    ai_experience: list[str] = Field(default_factory=list)


_EXTRACTION_PROMPT = """Extract a structured candidate profile from the CV below.

- experience_years: total years of professional experience, estimated from the work history.
- roles: job titles/role types this person is qualified for (e.g. "backend engineer", "full stack developer").
- skills: technical skills with an honest level (aware/commercial/strong/expert) based on how \
the CV describes the experience — don't inflate.
- experience: each past role, in reverse chronological order.
- achievements: standout, quantifiable accomplishments (not generic duties).
- domains: industry/domain experience (e.g. "fintech", "e-commerce").
- ai_experience: specific AI/ML/LLM-related experience, if any — empty list if none.

CV:
---
{cv_text}
---
"""


class CvService:
    def __init__(
        self,
        candidate_repository: CandidateRepository,
        llm_provider: LLMProvider | None = None,
    ):
        self._candidate_repository = candidate_repository
        self._llm_provider = llm_provider

    async def upload_cv(self, user_id: uuid.UUID, filename: str, content: bytes) -> CvDocument:
        raw_text = extract_text(filename, content)
        return await self._candidate_repository.save_cv_document(user_id, filename, raw_text)

    async def list_cvs(self, user_id: uuid.UUID) -> list[CvDocument]:
        return await self._candidate_repository.list_cv_documents(user_id)

    async def delete_cv(self, user_id: uuid.UUID, cv_document_id: uuid.UUID) -> bool:
        return await self._candidate_repository.delete_cv_document(user_id, cv_document_id)

    async def get_latest_profile(self, user_id: uuid.UUID) -> CandidateProfile | None:
        return await self._candidate_repository.get_latest_candidate_profile(user_id)

    async def correct_skill(
        self, user_id: uuid.UUID, override: SkillOverride
    ) -> CandidateProfile:
        """Record what the user says about one of their skills and write the
        profile revision that reflects it.

        The correction is stored separately from the snapshot so re-analyzing the
        CV re-applies it (see app/domain/candidates/skill_overrides.py), and the
        profile itself becomes a new immutable revision rather than being edited
        in place — the CV hasn't changed, but what the user says about it has,
        and matches scored against the old revision keep saying which one they
        used. Undoing a removal therefore means re-analyzing the CV: the snapshot
        chain only moves forward.
        """
        await self._candidate_repository.save_skill_override(user_id, override)

        profile = await self._candidate_repository.get_latest_candidate_profile(user_id)
        if profile is None:
            raise LookupError(f"user {user_id} has no analyzed CandidateProfile yet")

        corrected = replace(profile, skills=apply_overrides(profile.skills, [override]))
        return await self._candidate_repository.save_candidate_profile(
            user_id,
            uuid.UUID(profile.cv_document_id) if profile.cv_document_id else None,
            corrected,
        )

    async def analyze_cv(
        self, user_id: uuid.UUID, cv_document_id: uuid.UUID, cv_text: str
    ) -> CandidateProfile:
        if self._llm_provider is None:
            raise LlmNotConfigured(
                "no LLM provider configured — set LLM_PROVIDER and its credentials"
            )

        try:
            result = await self._llm_provider.structured_completion(
                _EXTRACTION_PROMPT.format(cv_text=cv_text), _ExtractedProfile
            )
        except Exception as exc:
            # The provider's own message can carry account ids, rate-limit headers
            # and request ids — it goes to the log, never to the HTTP response
            # (see app/services/ai_errors.py).
            logger.warning("CV analysis failed for user %s", user_id, exc_info=True)
            raise LlmCallFailed() from exc
        extracted = result.data

        profile = CandidateProfile(
            id="",  # assigned by the repository on save
            user_id=str(user_id),
            experience_years=extracted.experience_years,
            roles=extracted.roles,
            skills=apply_overrides(
                normalize(
                    CandidateSkill(name=skill.name, level=SkillLevel(skill.level), years=skill.years)
                    for skill in extracted.skills
                ),
                await self._candidate_repository.list_skill_overrides(user_id),
            ),
            experience=[
                ExperienceEntry(
                    company=entry.company,
                    title=entry.title,
                    start_date=entry.start_date,
                    end_date=entry.end_date,
                    description=entry.description,
                    skills=entry.skills,
                )
                for entry in extracted.experience
            ],
            achievements=extracted.achievements,
            domains=extracted.domains,
            ai_experience=extracted.ai_experience,
            generated_by=result.model_label,
        )
        return await self._candidate_repository.save_candidate_profile(
            user_id, cv_document_id, profile
        )
