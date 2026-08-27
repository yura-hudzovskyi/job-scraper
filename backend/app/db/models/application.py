"""ORM table for the application tracker (see docs/roadmap.md, Phase 5)."""

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class ApplicationModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "applications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    canonical_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_jobs.id"))
    status: Mapped[str]
