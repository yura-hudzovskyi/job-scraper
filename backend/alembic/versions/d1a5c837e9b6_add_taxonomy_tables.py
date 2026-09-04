"""taxonomy concepts, relations, linked mentions and the unmapped queue

Revision ID: d1a5c837e9b6
Revises: c8e3a1d76f42
Create Date: 2026-09-04 17:10:00.000000

Phase 4 storage. ESCO is imported from a pinned release rather than queried live
(spec 9.1), so these hold a local copy stamped with the version it came from.

Two versions coexist deliberately: a profile extracted under one release has to
stay readable after the next is imported, which only works if the concepts it
points at are still present. `taxonomy_versions` is what makes an import atomic —
rows land under a version that is `importing`, get counted and validated, and
only then does it become `active`.

Additive and reversible: nothing existing references these tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a5c837e9b6"
down_revision: str | Sequence[str] | None = "c8e3a1d76f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taxonomy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("source_checksum", sa.String(), nullable=True),
        sa.Column(
            "languages", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default="[]",
        ),
        sa.Column("concept_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="importing"),
        sa.Column("failure_detail", sa.String(), nullable=True),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("namespace", "version", name="uq_taxonomy_versions_namespace_version"),
        sa.CheckConstraint(
            "status IN ('importing', 'ready', 'active', 'superseded', 'failed')",
            name="ck_taxonomy_versions_status",
        ),
    )

    op.create_table(
        "taxonomy_concepts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("taxonomy_version", sa.String(), nullable=False),
        sa.Column("concept_type", sa.String(), nullable=False),
        sa.Column("preferred_label", sa.String(), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.UniqueConstraint(
            "namespace", "external_id", "taxonomy_version", name="uq_taxonomy_concepts_identity"
        ),
    )
    op.create_index(
        "ix_taxonomy_concepts_version_status",
        "taxonomy_concepts",
        ["taxonomy_version", "status"],
    )

    op.create_table(
        "taxonomy_relations",
        sa.Column(
            "source_concept_id", sa.Uuid(), sa.ForeignKey("taxonomy_concepts.id"), nullable=False
        ),
        sa.Column(
            "target_concept_id", sa.Uuid(), sa.ForeignKey("taxonomy_concepts.id"), nullable=False
        ),
        sa.Column("relation_type", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_concept_id", "target_concept_id", "relation_type", name="pk_taxonomy_relations"
        ),
        sa.CheckConstraint(
            "relation_type IN ('broader', 'narrower', 'related', 'essential_for', "
            "'optional_for', 'same_as')",
            name="ck_taxonomy_relations_type",
        ),
    )
    op.create_index(
        "ix_taxonomy_relations_source_concept_id", "taxonomy_relations", ["source_concept_id"]
    )
    op.create_index(
        "ix_taxonomy_relations_target_concept_id", "taxonomy_relations", ["target_concept_id"]
    )

    op.create_table(
        "profile_concept_mentions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "profile_revision_id",
            sa.Uuid(),
            sa.ForeignKey("profile_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "concept_id", sa.Uuid(), sa.ForeignKey("taxonomy_concepts.id"), nullable=True
        ),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("normalized_text", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False, server_default="required"),
        sa.Column("link_status", sa.String(), nullable=False, server_default="unmapped"),
        sa.Column(
            "extraction_confidence", sa.Float(), nullable=False, server_default="1.0"
        ),
        sa.Column("link_score", sa.Float(), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "link_status IN ('linked', 'ambiguous', 'unmapped', 'manual')",
            name="ck_profile_concept_mentions_link_status",
        ),
        sa.CheckConstraint(
            "link_status <> 'linked' OR concept_id IS NOT NULL",
            name="ck_profile_concept_mentions_linked_has_concept",
        ),
        sa.CheckConstraint(
            "end_char IS NULL OR start_char IS NULL OR end_char > start_char",
            name="ck_profile_concept_mentions_span_ordered",
        ),
    )
    op.create_index(
        "ix_profile_concept_mentions_profile_revision_id",
        "profile_concept_mentions",
        ["profile_revision_id"],
    )
    op.create_index(
        "ix_profile_concept_mentions_concept_id", "profile_concept_mentions", ["concept_id"]
    )
    op.create_index(
        "ix_profile_concept_mentions_normalized", "profile_concept_mentions", ["normalized_text"]
    )

    op.create_table(
        "unmapped_mentions",
        sa.Column("normalized_text", sa.String(), primary_key=True),
        sa.Column("sample_raw_text", sa.String(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "promoted_concept_id", sa.Uuid(), sa.ForeignKey("taxonomy_concepts.id"), nullable=True
        ),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'promoted', 'ignored')", name="ck_unmapped_mentions_status"
        ),
    )
    op.create_index(
        "ix_unmapped_mentions_status_occurrences",
        "unmapped_mentions",
        ["status", "occurrences"],
    )


def downgrade() -> None:
    op.drop_index("ix_unmapped_mentions_status_occurrences", table_name="unmapped_mentions")
    op.drop_table("unmapped_mentions")
    op.drop_index(
        "ix_profile_concept_mentions_normalized", table_name="profile_concept_mentions"
    )
    op.drop_index(
        "ix_profile_concept_mentions_concept_id", table_name="profile_concept_mentions"
    )
    op.drop_index(
        "ix_profile_concept_mentions_profile_revision_id", table_name="profile_concept_mentions"
    )
    op.drop_table("profile_concept_mentions")
    op.drop_index("ix_taxonomy_relations_target_concept_id", table_name="taxonomy_relations")
    op.drop_index("ix_taxonomy_relations_source_concept_id", table_name="taxonomy_relations")
    op.drop_table("taxonomy_relations")
    op.drop_index("ix_taxonomy_concepts_version_status", table_name="taxonomy_concepts")
    op.drop_table("taxonomy_concepts")
    op.drop_table("taxonomy_versions")
