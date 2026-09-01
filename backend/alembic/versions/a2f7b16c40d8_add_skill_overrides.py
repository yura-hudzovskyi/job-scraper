"""add candidate_skill_overrides

Revision ID: a2f7b16c40d8
Revises: f1a4d0c72e39
Create Date: 2026-09-01 20:10:00.000000

Hand-written, same as the prior migrations. Phase 2 of docs/ai-pipeline-v3.md
(A4): a user's corrections to their own extracted skills outrank every automated
extraction and must survive reprocessing, so they live per user rather than
inside a CandidateProfile snapshot — a snapshot is one analysis, and the next
analysis would silently undo them.

unique(user_id, skill_key) is what makes re-editing the same skill replace the
previous decision instead of stacking another one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2f7b16c40d8"
down_revision: str | Sequence[str] | None = "f1a4d0c72e39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_skill_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("skill_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("years", sa.Float(), nullable=True),
        sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "skill_key"),
    )


def downgrade() -> None:
    op.drop_table("candidate_skill_overrides")
