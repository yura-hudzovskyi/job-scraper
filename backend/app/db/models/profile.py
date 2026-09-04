"""ORM table for extracted profile revisions — see app/domain/profiles/models.py.

Empty until Phase 3 puts an extractor behind it. It exists now because Phase 1 is
where versioning is settled: a profile written later without its schema version,
extractor model id and parent revision would be unreproducible, and adding those
columns after rows exist is the migration this phase is meant to avoid.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class ProfileRevisionModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "profile_revisions"
    __table_args__ = (
        CheckConstraint(
            "origin NOT IN ('neural_extraction', 'structural_extraction') "
            "OR extractor_model_id IS NOT NULL",
            name="ck_profile_revisions_extraction_names_its_model",
        ),
        CheckConstraint(
            "overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)",
            name="ck_profile_revisions_confidence_range",
        ),
    )

    document_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_revisions.id"), index=True
    )
    # "job" | "candidate"
    profile_kind: Mapped[str]
    # e.g. "job-profile/1.0" — the versioned Pydantic schema `extracted_profile`
    # validates against, so an old row can still be read by the code that wrote it.
    schema_version: Mapped[str]
    # "neural_extraction" | "user_override" | "migration"; see ProfileOrigin.
    origin: Mapped[str]
    # The revision this one supersedes — a user correction points at the extraction
    # it corrected, so the edit history is walkable rather than overwritten.
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profile_revisions.id"), default=None
    )
    # Pinned model id + revision of the extractor that produced this. Required for
    # origin = neural_extraction (check constraint above).
    extractor_model_id: Mapped[str | None] = mapped_column(default=None)

    extracted_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    overall_confidence: Mapped[float | None] = mapped_column(default=None)
    validation_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
