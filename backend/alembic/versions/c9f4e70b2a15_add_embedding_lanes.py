"""add embedding lanes and section vectors

Revision ID: c9f4e70b2a15
Revises: b8e5c2a91d47
Create Date: 2026-09-01 22:10:00.000000

Hand-written, same as the prior migrations. Phase 4 of docs/ai-pipeline-v3.md
(C2, 7): section vectors, each one naming the lane — the embedding model's own
vector space — it belongs to, so a query can never compare vectors from two
different models.

The `vector` extension ships with the pgvector/pgvector:pg16 image both compose
files already use, so this only has to enable it. The column is dimensionless
because lanes differ (384 locally, 1024 for BGE-M3/Voyage); that means no ANN
index, which is the intended trade at a few thousand jobs — see the model's
docstring.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f4e70b2a15"
down_revision: str | Sequence[str] | None = "b8e5c2a91d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "embedding_lanes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="building"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "document_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_type", sa.String(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(), nullable=False),
        sa.Column("lane_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("vector", Vector(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "document_type", "document_id", "section", "lane_id", name="uq_document_embeddings_slot"
        ),
    )
    # Every read is "this lane's vectors for these documents" or "this document's
    # stored hashes", and every cleanup is by document.
    op.create_index(
        "ix_document_embeddings_lane_type", "document_embeddings", ["lane_id", "document_type"]
    )
    op.create_index(
        "ix_document_embeddings_document", "document_embeddings", ["document_type", "document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_embeddings_document", table_name="document_embeddings")
    op.drop_index("ix_document_embeddings_lane_type", table_name="document_embeddings")
    op.drop_table("document_embeddings")
    op.drop_table("embedding_lanes")
    # The extension is left in place: other things may rely on it, and dropping a
    # database-wide extension is not this migration's business.
