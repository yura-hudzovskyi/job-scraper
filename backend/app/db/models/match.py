"""ORM table for JobMatch.

unique(user_id, canonical_job_id) keeps re-matching idempotent — a new run
updates the existing row instead of duplicating it.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class JobMatchModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("user_id", "canonical_job_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    canonical_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_jobs.id"))

    # False when a hard filter rejected the vacancy — `filter_reasons` then says
    # which of the user's own rules it broke.
    eligible: Mapped[bool]
    filter_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # The final 0-100 number, and the two raw signals it was built from. All
    # three are stored so the UI can show the arithmetic instead of a verdict:
    # relevance is NULL for a job the reranker never saw.
    score: Mapped[float]
    similarity: Mapped[float]
    relevance: Mapped[float | None] = mapped_column(default=None)
    rerank_position: Mapped[int | None] = mapped_column(default=None)

    recommendation: Mapped[str]
    # Which models produced this result. Read back from the row, so an old match
    # keeps naming the models that really ran after the config changes.
    embedding_model: Mapped[str | None] = mapped_column(default=None)
    rerank_model: Mapped[str | None] = mapped_column(default=None)
    rerank_weight: Mapped[float | None] = mapped_column(default=None)

    # The user's own Approve/Reject verdict from the Telegram buttons.
    # Deliberately excluded from the re-match upsert, so a new run never resets a
    # decision already made.
    decision: Mapped[str] = mapped_column(default="pending", server_default="pending")

    scored_at: Mapped[datetime] = mapped_column(server_default=func.now())
