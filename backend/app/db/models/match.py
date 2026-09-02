"""ORM table for JobMatch. unique(user_id, canonical_job_id) keeps rescoring idempotent
— re-evaluating a job for a user updates the existing row instead of duplicating."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class JobMatchModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("user_id", "canonical_job_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    canonical_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_jobs.id"))

    eligible: Mapped[bool]
    requirement_match: Mapped[float]
    practical_fit: Mapped[float]
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    strengths: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    gaps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    recommendation: Mapped[str | None] = mapped_column(default=None)
    # How much evidence stood behind the score (hybrid engine), and what the
    # result could not establish. Both are shown next to the score rather than
    # folded into it.
    confidence: Mapped[float | None] = mapped_column(default=None)
    risks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    llm_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # How this result was produced — see app/domain/matching/provenance.py. Kept
    # as a snapshot, so it keeps naming the models that really ran even after the
    # configured ones change.
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # The user's own Approve/Reject verdict (see MatchDecision) — set once via the
    # Telegram swipe buttons and deliberately excluded from MatchRepository.upsert's
    # rescore path, so re-scoring a job never silently resets a decision already made.
    decision: Mapped[str] = mapped_column(default="pending", server_default="pending")

    scored_at: Mapped[datetime] = mapped_column(server_default=func.now())
