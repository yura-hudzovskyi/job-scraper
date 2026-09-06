"""Field-level vectors — spec 10.2, 7.4.

One vector per document becomes one per field, so a vacancy can be retrieved on
its competencies without outshouting the company blurb that surrounds them. The
evaluation set is what asked for this: P@10 1.00 against Recall@100 0.58.

Existing rows are the `full_profile` field by definition — that is exactly what
they hold — so the default backfills them truthfully rather than marking them
unknown. Their `template_version` stays NULL because they predate templates, and
claiming a version they were not built under would make them look reproducible.

Revision ID: f3c7a9e21d58
Revises: e2b9d4f16c83
"""

import sqlalchemy as sa

from alembic import op

revision = "f3c7a9e21d58"
down_revision = "e2b9d4f16c83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_embeddings",
        sa.Column("field", sa.String(), server_default="full_profile", nullable=False),
    )
    op.add_column("document_embeddings", sa.Column("template_version", sa.String(), nullable=True))
    # Stored rather than derived so a mismatch is caught on write with a message
    # that names both sizes, instead of at query time as pgvector's "different
    # vector dimensions" from inside a cosine operator.
    op.add_column("document_embeddings", sa.Column("dimensions", sa.Integer(), nullable=True))
    op.execute("UPDATE document_embeddings SET dimensions = vector_dims(vector)")

    # The old key had no field, so a second field for the same document would
    # have overwritten the first.
    op.drop_constraint(
        "uq_document_embeddings_document_model", "document_embeddings", type_="unique"
    )
    op.create_unique_constraint(
        "uq_document_embeddings_identity",
        "document_embeddings",
        ["document_type", "document_id", "model", "field"],
    )
    op.create_index(
        "ix_document_embeddings_lookup",
        "document_embeddings",
        ["document_type", "model", "field"],
    )


def downgrade() -> None:
    # Field rows have to go before the old key can be restored: they are exactly
    # the rows it forbids. Only `full_profile` existed before this migration, so
    # nothing that predates it is lost.
    op.execute("DELETE FROM document_embeddings WHERE field <> 'full_profile'")
    op.drop_index("ix_document_embeddings_lookup", table_name="document_embeddings")
    op.drop_constraint("uq_document_embeddings_identity", "document_embeddings", type_="unique")
    op.create_unique_constraint(
        "uq_document_embeddings_document_model",
        "document_embeddings",
        ["document_type", "document_id", "model"],
    )
    op.drop_column("document_embeddings", "dimensions")
    op.drop_column("document_embeddings", "template_version")
    op.drop_column("document_embeddings", "field")
