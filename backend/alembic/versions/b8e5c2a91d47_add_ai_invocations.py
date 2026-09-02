"""add ai_invocations

Revision ID: b8e5c2a91d47
Revises: a2f7b16c40d8
Create Date: 2026-09-01 21:30:00.000000

Hand-written, same as the prior migrations. Phase 3 of docs/ai-pipeline-v3.md
(6.1): the durable half of the AI call ledger. The router buffers every call in
Redis and a scheduled task drains it here, so the daily limits in Settings can be
tuned against what actually happened rather than guessed at.

Append-only and pruned by that same task (30 days), so the index that matters is
on created_at — every read is "since when" and every delete is "older than".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5c2a91d47"
down_revision: str | Sequence[str] | None = "a2f7b16c40d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_ai_invocations_created_at", "ai_invocations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_invocations_created_at", table_name="ai_invocations")
    op.drop_table("ai_invocations")
