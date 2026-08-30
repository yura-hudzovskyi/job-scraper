"""add job_matches llm_assessment

Revision ID: a1f3c9d84b6e
Revises: c7940d19c304
Create Date: 2026-08-30 00:20:00.000000

Hand-written and verified the same way as the prior migrations. Stores the LLM's
full structured "should I apply?" verdict (see app/domain/matching/llm_reranker.py
and app/domain/matching/models.py::LlmAssessment) as one JSONB blob — same
convention as breakdown/strengths/gaps already on this table: point-in-time
snapshots, no query-by-subfield need, schema evolves without further migrations.
Nullable — only populated for Recommendation.APPLY matches when an LLM reranker
is configured (see MatchingService.should_i_apply).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c9d84b6e"
down_revision: str | Sequence[str] | None = "c7940d19c304"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_matches", sa.Column("llm_assessment", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_matches", "llm_assessment")
