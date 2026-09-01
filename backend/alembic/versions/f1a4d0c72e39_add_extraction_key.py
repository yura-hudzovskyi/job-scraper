"""add job_source_records skills_extraction_key

Revision ID: f1a4d0c72e39
Revises: e7c3a9d15b48
Create Date: 2026-09-01 19:45:00.000000

Hand-written, same as the prior migrations. Phase 2 of docs/ai-pipeline-v3.md (8):
records what each stored extraction was keyed on — the posting's own content hash
plus the extraction version — so a later pass over an unchanged posting returns
the stored requirements instead of spending a request re-deriving them.

NULL means "never extracted, or extracted before this existed", which simply
misses the cache once.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a4d0c72e39"
down_revision: str | Sequence[str] | None = "e7c3a9d15b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_source_records", sa.Column("skills_extraction_key", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("job_source_records", "skills_extraction_key")
