"""ORM tables for CV storage, LLM-extracted candidate profiles, and user preferences.

UserPreference is fully structured and needs no AI. CandidateProfile is extracted
from a CvDocument by an LLMProvider (see services/cv_service.py) — skills/experience/
roles are stored as JSONB rather than normalized child tables, matching how
job_source_records stores its (also LLM-extractable-in-the-future) skills list.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class CvDocumentModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "cv_documents"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str]
    raw_text: Mapped[str]
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CandidateProfileModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # Nullable + SET NULL: deleting the source CvDocument (see DELETE /api/cv/{cv_id})
    # must never delete or break a profile already extracted from it — a
    # CandidateProfile is a full point-in-time snapshot, it doesn't need the raw CV
    # text to stay meaningful, so this just severs the now-stale backref.
    cv_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cv_documents.id", ondelete="SET NULL"), default=None
    )

    experience_years: Mapped[float]
    roles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    skills: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    experience: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    achievements: Mapped[list[str]] = mapped_column(JSONB, default=list)
    domains: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ai_experience: Mapped[list[str]] = mapped_column(JSONB, default=list)
    generated_by: Mapped[str | None] = mapped_column(default=None)

    extracted_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UserPreferenceModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    desired_salary_usd: Mapped[int | None] = mapped_column(default=None)
    preferred_roles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    preferred_stack: Mapped[list[str]] = mapped_column(JSONB, default=list)
    acceptable_stack: Mapped[list[str]] = mapped_column(JSONB, default=list)
    blocked_stack: Mapped[list[str]] = mapped_column(JSONB, default=list)
    work_formats: Mapped[list[str]] = mapped_column(JSONB, default=list)
    locations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    max_required_experience: Mapped[float | None] = mapped_column(default=None)
    industries_blacklist: Mapped[list[str]] = mapped_column(JSONB, default=list)
    companies_blacklist: Mapped[list[str]] = mapped_column(JSONB, default=list)
