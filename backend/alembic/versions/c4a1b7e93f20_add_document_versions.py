"""add canonical job and candidate profile versions

Revision ID: c4a1b7e93f20
Revises: b3d8f4a1c9e2
Create Date: 2026-09-01 18:00:00.000000

Hand-written, same as the prior migrations. Phase 1 of docs/ai-pipeline-v3.md:
every match records which revision of the CV and of the job posting produced it,
so a result stays explainable after either document changes. The hash is the real
identity, `version`/`content_version` is the human-readable label — see
app/domain/versioning.py.

Existing rows get version 1 and a NULL hash: the hash is filled in the first time
each document is read by the pipeline again (a profile on its next analysis, a
job on its next scoring run).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a1b7e93f20"
down_revision: str | Sequence[str] | None = "b3d8f4a1c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("canonical_jobs", sa.Column("content_hash", sa.String(), nullable=True))
    op.add_column(
        "canonical_jobs",
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("candidate_profiles", sa.Column("content_hash", sa.String(), nullable=True))
    op.add_column(
        "candidate_profiles",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "version")
    op.drop_column("candidate_profiles", "content_hash")
    op.drop_column("canonical_jobs", "content_version")
    op.drop_column("canonical_jobs", "content_hash")
