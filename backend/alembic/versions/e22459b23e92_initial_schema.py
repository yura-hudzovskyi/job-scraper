"""initial schema

Revision ID: e22459b23e92
Revises:
Create Date: 2026-08-27 20:18:26.044529

Hand-written from app/db/models rather than `--autogenerate`, since generating this
against a live database wasn't available in the environment this was authored in.
Table definitions were cross-checked by compiling CreateTable(table) for every model
against the postgresql dialect offline — see the commit message for how to redo that
check. Table order follows Base.metadata.sorted_tables (topological, by FK).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e22459b23e92"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAMES_IN_DEPENDENCY_ORDER = [
    "canonical_jobs",
    "notification_deliveries",
    "raw_jobs",
    "scrape_runs",
    "users",
    "applications",
    "cv_documents",
    "job_matches",
    "job_source_records",
    "user_preferences",
]


def upgrade() -> None:
    op.create_table(
        "canonical_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.UniqueConstraint("notification_id", "channel"),
    )

    op.create_table(
        "raw_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "external_id"),
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("jobs_seen", sa.Integer(), nullable=False),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "canonical_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_jobs.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
    )

    op.create_table(
        "cv_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "job_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "canonical_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_jobs.id"),
            nullable=False,
        ),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("requirement_match", sa.Float(), nullable=False),
        sa.Column("practical_fit", sa.Float(), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(), nullable=False),
        sa.Column("strengths", postgresql.JSONB(), nullable=False),
        sa.Column("gaps", postgresql.JSONB(), nullable=False),
        sa.Column("recommendation", sa.String(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "canonical_job_id"),
    )

    op.create_table(
        "job_source_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "raw_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("raw_jobs.id"), nullable=False
        ),
        sa.Column(
            "canonical_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_jobs.id"),
            nullable=True,
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("employment_type", sa.String(), nullable=False),
        sa.Column("remote", sa.Boolean(), nullable=False),
        sa.Column("countries", postgresql.JSONB(), nullable=False),
        sa.Column("cities", postgresql.JSONB(), nullable=False),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(), nullable=True),
        sa.Column("seniority", sa.String(), nullable=True),
        sa.Column("required_experience_years", sa.Float(), nullable=True),
        sa.Column("skills", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "external_id"),
    )

    op.create_table(
        "user_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("desired_salary_usd", sa.Integer(), nullable=True),
        sa.Column("preferred_roles", postgresql.JSONB(), nullable=False),
        sa.Column("preferred_stack", postgresql.JSONB(), nullable=False),
        sa.Column("acceptable_stack", postgresql.JSONB(), nullable=False),
        sa.Column("blocked_stack", postgresql.JSONB(), nullable=False),
        sa.Column("work_formats", postgresql.JSONB(), nullable=False),
        sa.Column("locations", postgresql.JSONB(), nullable=False),
        sa.Column("max_required_experience", sa.Float(), nullable=True),
        sa.Column("industries_blacklist", postgresql.JSONB(), nullable=False),
        sa.Column("companies_blacklist", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    for table_name in reversed(_TABLE_NAMES_IN_DEPENDENCY_ORDER):
        op.drop_table(table_name)
