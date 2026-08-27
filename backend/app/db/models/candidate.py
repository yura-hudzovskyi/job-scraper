"""ORM tables for CV storage and user preferences.

CandidateProfile (skills/experience extracted from a CV) is a Phase 2 concern once an
LLM provider exists — see docs/roadmap.md. Phase 1 only stores the uploaded CV's raw
extracted text. UserPreference is fully structured and needs no AI.
"""

import uuid
from datetime import datetime

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
