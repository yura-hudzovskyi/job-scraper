"""Remember what a rerank score was computed from — spec 24.0 invariant 4.

Reranking every eligible vacancy is what makes the ranking good: the reranker is
the only stage that reads a CV and a vacancy together. Doing it every thirty
minutes for a corpus that has not changed is roughly 2.9 million tokens a run,
for answers already known.

These two hashes are what makes the difference between the first full pass and
every later one. A stored `relevance` stays valid while the CV, the vacancy text
and the model are all unchanged, which for a settled corpus is almost all of it.

On the match row rather than in a cache table of its own, so it is deleted with
the match it belongs to — a vacancy purged by retention takes its score with it,
and there is no second place to remember to clean up.

Revision ID: a7d2f4b81e69
Revises: f3c7a9e21d58
"""

import sqlalchemy as sa

from alembic import op

revision = "a7d2f4b81e69"
down_revision = "f3c7a9e21d58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL on every existing row, which is truthful: nothing has been reranked
    # yet. The batch that was supposed to do it exceeded the provider's token
    # limit on every run and the 400 was swallowed, so `relevance` is NULL for
    # all 1934 matches in production.
    op.add_column("job_matches", sa.Column("rerank_query_hash", sa.String(), nullable=True))
    op.add_column("job_matches", sa.Column("rerank_document_hash", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_matches", "rerank_document_hash")
    op.drop_column("job_matches", "rerank_query_hash")
