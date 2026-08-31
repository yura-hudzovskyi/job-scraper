"""add job_matches scored_by

Revision ID: f2a6c1d0b3e4
Revises: e354880e6204
Create Date: 2026-08-31 13:00:00.000000

Hand-written and verified the same way as the prior migrations. Records which
pipeline produced a match's score — "AI (<model label>)" when AiMatcher's single
structured-JSON call decided it (see app/domain/matching/ai_matcher.py), or
"deterministic" when it fell back to the filters -> weighted-score -> semantic ->
skill pipeline (no AI configured, or the AI call failed/timed out). Same
point-in-time-snapshot treatment as skills_source/llm_assessment already on this
table (see 3087399dee38_add_job_matches_skills_source.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a6c1d0b3e4"
down_revision: str | Sequence[str] | None = "e354880e6204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_matches", sa.Column("scored_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_matches", "scored_by")
