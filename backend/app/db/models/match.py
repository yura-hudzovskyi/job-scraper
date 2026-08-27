"""ORM table for JobMatch. unique(user_id, canonical_job_id) keeps rescoring idempotent."""

import uuid

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobMatchModel(Base):
    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("user_id", "canonical_job_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]
    canonical_job_id: Mapped[uuid.UUID]
    requirement_match: Mapped[float]
    practical_fit: Mapped[float]
