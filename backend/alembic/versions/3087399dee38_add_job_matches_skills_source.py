"""add job_matches skills_source

Revision ID: 3087399dee38
Revises: 5807259287bf
Create Date: 2026-08-30 00:10:00.000000

Hand-written and verified the same way as the prior migrations. A snapshot of
which LLM extracted the job's skills at the moment this match was scored (copied
from job_source_records.skills_extracted_by, see
app/domain/matching/service.py::evaluate) — stored here rather than joined live,
consistent with breakdown/strengths/gaps already being point-in-time snapshots on
this table rather than recomputed from live data on read.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3087399dee38"
down_revision: str | Sequence[str] | None = "5807259287bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_matches", sa.Column("skills_source", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("job_matches", "skills_source")
