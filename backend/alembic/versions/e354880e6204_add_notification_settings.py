"""add notification_settings

Revision ID: e354880e6204
Revises: a1f3c9d84b6e
Create Date: 2026-08-31 12:00:00.000000

Hand-written and verified the same way as the prior migrations. One row per user,
overriding NotificationPolicyConfig's hardcoded defaults (see
app/domain/notifications/policy.py) — created on first save from the Settings
page's new "Notification thresholds" section. Server-side defaults on every column
mirror NotificationPolicyConfig's dataclass defaults exactly, so a row inserted with
only user_id set behaves identically to today's hardcoded NotificationPolicy().
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e354880e6204"
down_revision: str | Sequence[str] | None = "a1f3c9d84b6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("immediate_threshold", sa.Float(), server_default="85.0", nullable=False),
        sa.Column("conditional_threshold", sa.Float(), server_default="75.0", nullable=False),
        sa.Column("digest_threshold", sa.Float(), server_default="65.0", nullable=False),
        sa.Column(
            "strong_component_threshold", sa.Float(), server_default="90.0", nullable=False
        ),
        sa.Column("quiet_hours_start", sa.Integer(), server_default="22", nullable=False),
        sa.Column("quiet_hours_end", sa.Integer(), server_default="8", nullable=False),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("notification_settings")
