"""ORM table for the application tracker (see docs/roadmap.md, Phase 5)."""

import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApplicationModel(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]
    canonical_job_id: Mapped[uuid.UUID]
    status: Mapped[str]
