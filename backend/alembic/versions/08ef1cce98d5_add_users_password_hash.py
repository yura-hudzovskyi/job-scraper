"""add users password_hash

Revision ID: 08ef1cce98d5
Revises: aba83f801c35
Create Date: 2026-08-29 00:00:00.000000

Hand-written and verified the same way as the prior two migrations. Adds real
credentials to the `users` table for the auth rewrite — see
app/services/auth_service.py. Before running this against an existing database that
predates auth (i.e. has rows created by the old default-user shortcut), clear the
`users` table first: those rows have no password and would otherwise permanently
block registering with the same email once `password_hash` is NOT NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08ef1cce98d5"
down_revision: str | Sequence[str] | None = "aba83f801c35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
