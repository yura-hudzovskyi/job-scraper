"""add job_matches confidence and risks

Revision ID: d3b7a10f96c4
Revises: c9f4e70b2a15
Create Date: 2026-09-01 23:40:00.000000

Hand-written, same as the prior migrations. Phase 6 of docs/ai-pipeline-v3.md
(G1): the hybrid engine reports how much evidence stood behind a score and what
it could not establish, and both are shown next to the score rather than folded
into it — an 84 backed by two extracted requirements and an 84 backed by twelve
are not the same claim.

Both stay NULL/empty for matches scored before the hybrid engine, which the UI
renders as "not recorded" rather than as a confident zero.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3b7a10f96c4"
down_revision: str | Sequence[str] | None = "c9f4e70b2a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_matches", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "job_matches",
        sa.Column(
            "risks", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )


def downgrade() -> None:
    op.drop_column("job_matches", "risks")
    op.drop_column("job_matches", "confidence")
