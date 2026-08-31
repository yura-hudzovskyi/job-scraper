"""add job_matches decision

Revision ID: b3d8f4a1c9e2
Revises: a7c1e9f4b2d6
Create Date: 2026-08-31 15:00:00.000000

Hand-written and verified the same way as the prior migrations. Backs the
Telegram swipe UI (Approve/Reject buttons, see telegram_provider.py and
workers/tasks/telegram_poll.py) — independent of `recommendation` (the pipeline's
own opinion) and never touched by a rescore, since MatchRepository.upsert
excludes this column from its on_conflict_do_update set.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d8f4a1c9e2"
down_revision: str | Sequence[str] | None = "a7c1e9f4b2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_matches",
        sa.Column("decision", sa.String(), nullable=False, server_default="pending"),
    )


def downgrade() -> None:
    op.drop_column("job_matches", "decision")
