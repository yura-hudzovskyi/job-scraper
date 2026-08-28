"""add candidate profiles

Revision ID: aba83f801c35
Revises: e22459b23e92
Create Date: 2026-08-28 09:53:41.058214

Hand-written and verified the same way as the initial schema (e22459b23e92) — see
that migration's docstring and commit message for how.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "aba83f801c35"
down_revision: str | Sequence[str] | None = "e22459b23e92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "cv_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cv_documents.id"),
            nullable=False,
        ),
        sa.Column("experience_years", sa.Float(), nullable=False),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("skills", postgresql.JSONB(), nullable=False),
        sa.Column("experience", postgresql.JSONB(), nullable=False),
        sa.Column("achievements", postgresql.JSONB(), nullable=False),
        sa.Column("domains", postgresql.JSONB(), nullable=False),
        sa.Column("ai_experience", postgresql.JSONB(), nullable=False),
        sa.Column(
            "extracted_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("candidate_profiles")
