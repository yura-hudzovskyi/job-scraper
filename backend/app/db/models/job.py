"""ORM tables for RawJob, CanonicalJob, JobSourceRecord, JobVersion.

unique(source, external_job_id) on job_source_records is what makes re-scraping
idempotent — see docs/domain-model.md.
"""

import uuid

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CanonicalJobModel(Base):
    __tablename__ = "canonical_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class JobSourceRecordModel(Base):
    __tablename__ = "job_source_records"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    canonical_job_id: Mapped[uuid.UUID]
    source: Mapped[str]
    external_id: Mapped[str]
    url: Mapped[str]
