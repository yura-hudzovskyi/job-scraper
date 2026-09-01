"""add job_source_records category

Revision ID: e7c3a9d15b48
Revises: d5b2c8f14a63
Create Date: 2026-09-01 19:15:00.000000

Hand-written, same as the prior migrations. Phase 2 of docs/ai-pipeline-v3.md:
the extraction call that already reads a posting now also classifies the role, so
phase 4's confidence-aware category gate has something to work with. Both columns
stay NULL for jobs extracted before this, and a NULL category never filters
anything out.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c3a9d15b48"
down_revision: str | Sequence[str] | None = "d5b2c8f14a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_source_records", sa.Column("category", sa.String(), nullable=True))
    op.add_column(
        "job_source_records", sa.Column("category_confidence", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("job_source_records", "category_confidence")
    op.drop_column("job_source_records", "category")
