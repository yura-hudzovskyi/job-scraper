"""add job_matches relevance from the retrieval/rerank pass

Revision ID: e1c8f5a20d74
Revises: d3b7a10f96c4
Create Date: 2026-09-02 15:10:00.000000

Hand-written, same as the prior migrations. The retrieval/rerank pass
(app/workers/tasks/retrieve.py) ranks the whole corpus for one candidate and
leaves its calibrated relevance here, where the next scoring run picks it up as
the reranker input the hybrid engine was substituting semantic similarity for.

Both columns are written only by that pass and deliberately excluded from the
scoring upsert, so a rescore never wipes an ordering that cost provider calls.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1c8f5a20d74"
down_revision: str | Sequence[str] | None = "d3b7a10f96c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_matches", sa.Column("relevance", sa.Float(), nullable=True))
    op.add_column("job_matches", sa.Column("relevance_model", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_matches", "relevance_model")
    op.drop_column("job_matches", "relevance")
