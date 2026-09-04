"""document/profile revisions, blocks, transitions and the model registry

Revision ID: a4f1e6c73b28
Revises: f4c81a2e5b90
Create Date: 2026-09-04 11:20:00.000000

Phase 1 of the Universal Job-Candidate Matching Engine spec: the storage
foundation the extractor (Phase 3) and the concept linker (Phase 4) will write
into. Nothing reads these tables yet — the running pipeline
(scrape -> embed -> match -> notify) is untouched by this migration.

Layered over the existing identity tables rather than replacing them: a revision
points at a `job_source_records` row or a `cv_documents` row, both of which
already carry the unique constraints that make re-scraping idempotent. There is
deliberately no `source_items` table, which would have been a third name for the
identity those two already own (spec 3.4.3).

The backfill matters more than the DDL. Every existing job source record and CV
gets revision 1 in status `searchable`, so the corpus does not spend the window
between this migration and Phase 2 looking unprocessed. `content_hash` is
computed in SQL from the same text the revision stores, so a Phase 2 re-ingest of
genuinely unchanged text matches it and creates nothing.

Reversible: downgrade drops the four tables and their audit trail. No existing
table gains or loses a column, so a downgrade loses only data this migration
created.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f1e6c73b28"
down_revision: str | Sequence[str] | None = "f4c81a2e5b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entity_kind", sa.String(), nullable=False),
        sa.Column(
            "job_source_record_id",
            sa.Uuid(),
            sa.ForeignKey("job_source_records.id"),
            nullable=True,
        ),
        sa.Column("cv_document_id", sa.Uuid(), sa.ForeignKey("cv_documents.id"), nullable=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("parsed_text", sa.String(), nullable=True),
        sa.Column("language_code", sa.String(), nullable=True),
        sa.Column("parser_name", sa.String(), nullable=True),
        sa.Column("parser_version", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_detail", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "(job_source_record_id IS NULL) <> (cv_document_id IS NULL)",
            name="ck_document_revisions_exactly_one_owner",
        ),
        sa.CheckConstraint(
            "(entity_kind = 'job' AND job_source_record_id IS NOT NULL) "
            "OR (entity_kind = 'candidate' AND cv_document_id IS NOT NULL)",
            name="ck_document_revisions_owner_matches_kind",
        ),
        sa.CheckConstraint("revision_no >= 1", name="ck_document_revisions_revision_no_positive"),
        sa.UniqueConstraint(
            "job_source_record_id", "revision_no", name="uq_document_revisions_job_revision_no"
        ),
        sa.UniqueConstraint(
            "cv_document_id", "revision_no", name="uq_document_revisions_cv_revision_no"
        ),
        sa.UniqueConstraint(
            "job_source_record_id", "content_hash", name="uq_document_revisions_job_content_hash"
        ),
        sa.UniqueConstraint(
            "cv_document_id", "content_hash", name="uq_document_revisions_cv_content_hash"
        ),
    )
    op.create_index(
        "ix_document_revisions_job_source_record_id",
        "document_revisions",
        ["job_source_record_id"],
    )
    op.create_index(
        "ix_document_revisions_cv_document_id", "document_revisions", ["cv_document_id"]
    )
    op.create_index("ix_document_revisions_status", "document_revisions", ["status"])

    op.create_table(
        "document_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_revision_id",
            sa.Uuid(),
            sa.ForeignKey("document_revisions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("start_char >= 0", name="ck_document_blocks_start_non_negative"),
        sa.CheckConstraint("end_char > start_char", name="ck_document_blocks_end_after_start"),
        sa.UniqueConstraint(
            "document_revision_id", "ordinal", name="uq_document_blocks_revision_ordinal"
        ),
    )
    op.create_index(
        "ix_document_blocks_document_revision_id", "document_blocks", ["document_revision_id"]
    )

    op.create_table(
        "document_revision_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_revision_id",
            sa.Uuid(),
            sa.ForeignKey("document_revisions.id"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_document_revision_transitions_document_revision_id",
        "document_revision_transitions",
        ["document_revision_id"],
    )

    op.create_table(
        "profile_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_revision_id",
            sa.Uuid(),
            sa.ForeignKey("document_revisions.id"),
            nullable=False,
        ),
        sa.Column("profile_kind", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column(
            "parent_revision_id", sa.Uuid(), sa.ForeignKey("profile_revisions.id"), nullable=True
        ),
        sa.Column("extractor_model_id", sa.String(), nullable=True),
        sa.Column(
            "extracted_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("overall_confidence", sa.Float(), nullable=True),
        sa.Column(
            "validation_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "origin <> 'neural_extraction' OR extractor_model_id IS NOT NULL",
            name="ck_profile_revisions_extraction_names_its_model",
        ),
        sa.CheckConstraint(
            "overall_confidence IS NULL OR (overall_confidence >= 0 AND overall_confidence <= 1)",
            name="ck_profile_revisions_confidence_range",
        ),
    )
    op.create_index(
        "ix_profile_revisions_document_revision_id", "profile_revisions", ["document_revision_id"]
    )

    op.create_table(
        "model_registry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("deployment", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("revision", sa.String(), nullable=True),
        sa.Column("license", sa.String(), nullable=True),
        sa.Column("runtime_backend", sa.String(), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "deployment <> 'self_hosted' OR revision IS NOT NULL",
            name="ck_model_registry_self_hosted_is_pinned",
        ),
    )

    # --- backfill ------------------------------------------------------------
    # One revision per document already on file, in the state the pipeline has in
    # practice already put it in: its text is stored, searchable and matched
    # against today. Marking these `received` instead would make the whole corpus
    # look like a processing backlog the moment Phase 2 starts reading status.
    #
    # `origin` for these is `migration` when profiles arrive — nothing extracted
    # them, and a later reader must be able to tell that apart from a real
    # extraction (see ProfileOrigin).
    #
    # The hash is sha256 over the same text the row stores. Phase 2 must use the
    # same input for an unchanged document to hash equal and create no revision;
    # if it ends up normalizing first, it re-hashes these rows in its own
    # migration rather than silently doubling every document.
    op.execute(
        """
        INSERT INTO document_revisions (
            id, entity_kind, job_source_record_id, revision_no,
            content_hash, raw_text, status, created_at
        )
        SELECT
            gen_random_uuid(), 'job', r.id, 1,
            encode(sha256(convert_to(r.description, 'UTF8')), 'hex'),
            r.description, 'searchable', r.normalized_at
        FROM job_source_records r
        """
    )
    # `mime_type` stays NULL rather than being filled with the filename: the CV
    # table stores a name, not a media type, and deriving one from the extension
    # is the parser's job in Phase 2. A column holding the wrong kind of value is
    # worse than an empty one, because the next reader believes it.
    op.execute(
        """
        INSERT INTO document_revisions (
            id, entity_kind, cv_document_id, revision_no,
            content_hash, raw_text, status, created_at
        )
        SELECT
            gen_random_uuid(), 'candidate', c.id, 1,
            encode(sha256(convert_to(c.raw_text, 'UTF8')), 'hex'),
            c.raw_text, 'searchable', c.uploaded_at
        FROM cv_documents c
        """
    )
    # Every state a revision has been in is auditable, including the one it was
    # born in — a backfilled row says so rather than pretending it was ingested.
    op.execute(
        """
        INSERT INTO document_revision_transitions (
            id, document_revision_id, from_status, to_status, reason, occurred_at
        )
        SELECT gen_random_uuid(), d.id, NULL, 'searchable', 'backfilled', d.created_at
        FROM document_revisions d
        """
    )


def downgrade() -> None:
    op.drop_table("model_registry")
    op.drop_index("ix_profile_revisions_document_revision_id", table_name="profile_revisions")
    op.drop_table("profile_revisions")
    op.drop_index(
        "ix_document_revision_transitions_document_revision_id",
        table_name="document_revision_transitions",
    )
    op.drop_table("document_revision_transitions")
    op.drop_index("ix_document_blocks_document_revision_id", table_name="document_blocks")
    op.drop_table("document_blocks")
    op.drop_index("ix_document_revisions_status", table_name="document_revisions")
    op.drop_index("ix_document_revisions_cv_document_id", table_name="document_revisions")
    op.drop_index("ix_document_revisions_job_source_record_id", table_name="document_revisions")
    op.drop_table("document_revisions")
