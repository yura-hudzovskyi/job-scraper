"""ORM tables for the Raw -> Normalized -> Canonical job pipeline (docs/domain-model.md).

unique(source, external_id) on both raw_jobs and job_source_records is what makes
re-scraping idempotent — a repeated fetch upserts instead of duplicating.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class RawJobModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "raw_jobs"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    source: Mapped[str]
    external_id: Mapped[str]
    url: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CanonicalJobModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "canonical_jobs"

    title: Mapped[str]
    company: Mapped[str]
    description: Mapped[str]
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())


class JobSourceRecordModel(UUIDPrimaryKeyMixin, Base):
    """A NormalizedJob as persisted, before/after being attached to a CanonicalJob."""

    __tablename__ = "job_source_records"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    raw_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_jobs.id"))
    canonical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("canonical_jobs.id"), default=None
    )

    source: Mapped[str]
    external_id: Mapped[str]
    url: Mapped[str]

    title: Mapped[str]
    company: Mapped[str]
    description: Mapped[str]
    employment_type: Mapped[str]

    remote: Mapped[bool]
    countries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    cities: Mapped[list[str]] = mapped_column(JSONB, default=list)

    salary_min: Mapped[float | None] = mapped_column(default=None)
    salary_max: Mapped[float | None] = mapped_column(default=None)
    salary_currency: Mapped[str | None] = mapped_column(default=None)

    seniority: Mapped[str | None] = mapped_column(default=None)
    required_experience_years: Mapped[float | None] = mapped_column(default=None)
    skills: Mapped[list[dict]] = mapped_column(JSONB, default=list)

    normalized_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ScrapeRunModel(UUIDPrimaryKeyMixin, Base):
    """Per-source scrape execution record — see docs/source-adapters.md."""

    __tablename__ = "scrape_runs"

    source: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    pages: Mapped[int] = mapped_column(default=0)
    jobs_seen: Mapped[int] = mapped_column(default=0)
    new_count: Mapped[int] = mapped_column(default=0)
    updated_count: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
