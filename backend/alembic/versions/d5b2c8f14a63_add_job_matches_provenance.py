"""replace job_matches attribution columns with provenance

Revision ID: d5b2c8f14a63
Revises: c4a1b7e93f20
Create Date: 2026-09-01 18:30:00.000000

Hand-written, same as the prior migrations. Phase 1 of docs/ai-pipeline-v3.md:
`scored_by` and `skills_source` were two loose strings answering a fraction of
"how was this produced" — they're replaced by one provenance snapshot (engine,
analysis level, CV/job revision, models, fallback reason, pipeline versions), see
app/domain/matching/provenance.py.

Existing rows keep their score and lose only the two attribution strings; they
read back with no provenance until rescored, which the UI renders as "not
recorded" rather than inventing one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b2c8f14a63"
down_revision: str | Sequence[str] | None = "c4a1b7e93f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_matches", sa.Column("provenance", postgresql.JSONB(), nullable=True))
    op.drop_column("job_matches", "scored_by")
    op.drop_column("job_matches", "skills_source")


def downgrade() -> None:
    op.add_column("job_matches", sa.Column("skills_source", sa.String(), nullable=True))
    op.add_column("job_matches", sa.Column("scored_by", sa.String(), nullable=True))
    op.drop_column("job_matches", "provenance")
