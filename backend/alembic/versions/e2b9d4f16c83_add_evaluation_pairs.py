"""Judged candidate-vacancy pairs — the evaluation set of spec 20.1.

Nothing reads this table yet, and that is the point of adding it now: 20.1 says
annotation is the critical path and belongs in the early phases, because the
recall gate, the confidence thresholds, the `unknown` prior and calibration are
all tuned on it. A table that exists is a table a person can start filling.

Revision ID: e2b9d4f16c83
Revises: d1a5c837e9b6
"""

import sqlalchemy as sa

from alembic import op

revision = "e2b9d4f16c83"
down_revision = "d1a5c837e9b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_pairs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_revision_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_job_id", sa.Uuid(), nullable=False),
        sa.Column("job_revision_id", sa.Uuid(), nullable=True),
        # 0 irrelevant, 1 weak, 2 relevant, 3 strong. NULL until judged, which
        # is the state most rows are in most of the time.
        sa.Column("label", sa.Integer(), nullable=True),
        sa.Column("annotator", sa.String(), nullable=True),
        sa.Column("annotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tier", sa.String(), server_default="seed", nullable=False),
        sa.Column("sampled_from", sa.String(), server_default="unknown", nullable=False),
        sa.Column("system_score", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["candidate_revision_id"], ["document_revisions.id"]),
        sa.ForeignKeyConstraint(["canonical_job_id"], ["canonical_jobs.id"]),
        sa.ForeignKeyConstraint(["job_revision_id"], ["document_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_revision_id", "canonical_job_id", name="uq_evaluation_pairs_candidate_job"
        ),
        sa.CheckConstraint(
            "label IS NULL OR label BETWEEN 0 AND 3", name="ck_evaluation_pairs_label_range"
        ),
        # A label with no timestamp, or a timestamp with no label, is a row that
        # cannot say whether anyone judged it.
        sa.CheckConstraint(
            "(label IS NULL) = (annotated_at IS NULL)",
            name="ck_evaluation_pairs_label_and_time_agree",
        ),
        sa.CheckConstraint("tier IN ('seed', 'core', 'full')", name="ck_evaluation_pairs_tier"),
    )
    op.create_index(
        "ix_evaluation_pairs_candidate_revision_id", "evaluation_pairs", ["candidate_revision_id"]
    )
    op.create_index("ix_evaluation_pairs_tier_label", "evaluation_pairs", ["tier", "label"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_pairs_tier_label", table_name="evaluation_pairs")
    op.drop_index("ix_evaluation_pairs_candidate_revision_id", table_name="evaluation_pairs")
    op.drop_table("evaluation_pairs")
