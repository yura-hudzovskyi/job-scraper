"""ORM tables for immutable document revisions — see docs/domain-model.md.

Layered *over* the existing `job_source_records` / `cv_documents` rather than
replacing them: those two already carry the source identity and the unique
constraints that make re-scraping idempotent, so a second identity table would
be a duplicate with a different name. A revision row therefore points at one of
them, and the check constraint enforces "exactly one".

No `ondelete=` anywhere, matching the rest of the schema: deletion order is
explicit in the services that do it (JobRetentionService, SystemService), because
the order is the part that can be wrong and a silent cascade hides it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class DocumentRevisionModel(UUIDPrimaryKeyMixin, Base):
    """One immutable version of a source document.

    `unique(owner, content_hash)` is what makes "the scrape found nothing new"
    a database fact rather than an application convention — a re-fetch of
    unchanged text cannot create a second row even if a caller tries.
    """

    __tablename__ = "document_revisions"
    __table_args__ = (
        CheckConstraint(
            "(job_source_record_id IS NULL) <> (cv_document_id IS NULL)",
            name="ck_document_revisions_exactly_one_owner",
        ),
        CheckConstraint(
            "(entity_kind = 'job' AND job_source_record_id IS NOT NULL) "
            "OR (entity_kind = 'candidate' AND cv_document_id IS NOT NULL)",
            name="ck_document_revisions_owner_matches_kind",
        ),
        CheckConstraint("revision_no >= 1", name="ck_document_revisions_revision_no_positive"),
        UniqueConstraint(
            "job_source_record_id", "revision_no", name="uq_document_revisions_job_revision_no"
        ),
        UniqueConstraint(
            "cv_document_id", "revision_no", name="uq_document_revisions_cv_revision_no"
        ),
        UniqueConstraint(
            "job_source_record_id", "content_hash", name="uq_document_revisions_job_content_hash"
        ),
        UniqueConstraint(
            "cv_document_id", "content_hash", name="uq_document_revisions_cv_content_hash"
        ),
        Index("ix_document_revisions_status", "status"),
    )

    # "job" | "candidate" — which of the two owner FKs below is set.
    entity_kind: Mapped[str]
    job_source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_source_records.id"), default=None, index=True
    )
    cv_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cv_documents.id"), default=None, index=True
    )

    revision_no: Mapped[int]
    # Hash of the normalized-but-not-semantically-modified text this revision was
    # built from. The identity of a version, not a security boundary.
    content_hash: Mapped[str]

    mime_type: Mapped[str | None] = mapped_column(default=None)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    raw_text: Mapped[str]
    # Populated by block parsing in Phase 2. Offsets on document_blocks and on any
    # later evidence span are relative to this text, so it must never be rewritten.
    parsed_text: Mapped[str | None] = mapped_column(default=None)
    language_code: Mapped[str | None] = mapped_column(default=None)

    parser_name: Mapped[str | None] = mapped_column(default=None)
    parser_version: Mapped[str | None] = mapped_column(default=None)

    # See app/domain/documents/models.py RevisionStatus. Only "searchable" may be
    # used for matching; transitions are validated against ALLOWED_TRANSITIONS.
    status: Mapped[str] = mapped_column(default="received", server_default="received")
    failure_code: Mapped[str | None] = mapped_column(default=None)
    failure_detail: Mapped[str | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DocumentBlockModel(UUIDPrimaryKeyMixin, Base):
    """A parsed span of one revision, with global offsets into its `parsed_text`.

    Populated in Phase 2. Kept in its own table rather than as JSON on the
    revision because evidence spans (Phase 3) reference blocks by id, and a
    foreign key is the only version of that reference that can't dangle.
    """

    __tablename__ = "document_blocks"
    __table_args__ = (
        CheckConstraint("start_char >= 0", name="ck_document_blocks_start_non_negative"),
        CheckConstraint("end_char > start_char", name="ck_document_blocks_end_after_start"),
        UniqueConstraint(
            "document_revision_id", "ordinal", name="uq_document_blocks_revision_ordinal"
        ),
    )

    document_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_revisions.id"), index=True
    )
    ordinal: Mapped[int]
    # See app/domain/documents/models.py BlockType — layout, never meaning.
    block_type: Mapped[str] = mapped_column(default="unknown")
    text: Mapped[str]
    start_char: Mapped[int]
    end_char: Mapped[int]
    page: Mapped[int | None] = mapped_column(default=None)
    layout: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class DocumentRevisionTransitionModel(UUIDPrimaryKeyMixin, Base):
    """Append-only audit of every status change a revision went through.

    In Postgres rather than the logs for the same reason `pipeline_runs` is: a
    revision stuck in `failed` has to be able to answer "how did it get here" long
    after the log line has rotated away. `from_status` is NULL for the row that
    records a revision being created.
    """

    __tablename__ = "document_revision_transitions"

    document_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_revisions.id"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(default=None)
    to_status: Mapped[str]
    # Why it moved: a failure code, "reprocess", the task that drove it. Free text
    # on purpose — this is a breadcrumb for a human reading a stuck revision.
    reason: Mapped[str | None] = mapped_column(default=None)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
