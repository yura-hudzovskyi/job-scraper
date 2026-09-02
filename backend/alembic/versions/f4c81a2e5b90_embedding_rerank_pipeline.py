"""Embedding + rerank pipeline: drop the LLM layer, one vector per document

Removes every table and column that only existed to support LLM extraction,
enrichment and multi-lane embeddings, and reshapes what's left around the two
signals the pipeline now runs on: a cosine similarity and a rerank relevance.

The job/match/embedding data this drops cannot be reconstructed from the new
schema, and re-deriving it means one scrape plus one embedding pass, so
downgrade() rebuilds the old shape empty rather than pretending to restore it.

Revision ID: f4c81a2e5b90
Revises: e1c8f5a20d74
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4c81a2e5b90"
down_revision: str | Sequence[str] | None = "e1c8f5a20d74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- gone entirely -------------------------------------------------------
    # Every match is rebuilt by one matching pass, so old rows are dropped rather
    # than migrated into a scoring model that never produced them.
    op.execute("DELETE FROM notification_deliveries")
    op.execute("DELETE FROM notifications")
    op.execute("DELETE FROM job_matches")
    op.execute("DELETE FROM document_embeddings")

    op.drop_table("ai_invocations")
    op.drop_table("candidate_skill_overrides")
    op.drop_table("candidate_profiles")
    op.drop_table("applications")
    op.drop_table("embedding_lanes")

    # --- document_embeddings: lanes and sections collapse into one vector ----
    op.drop_constraint("uq_document_embeddings_slot", "document_embeddings", type_="unique")
    op.drop_column("document_embeddings", "section")
    op.drop_column("document_embeddings", "lane_id")
    op.drop_column("document_embeddings", "document_version")
    op.add_column("document_embeddings", sa.Column("model", sa.String(), nullable=False))
    op.create_unique_constraint(
        "uq_document_embeddings_document_model",
        "document_embeddings",
        ["document_type", "document_id", "model"],
    )

    # --- job_matches: two signals and the weight between them ----------------
    for column in (
        "requirement_match",
        "practical_fit",
        "breakdown",
        "strengths",
        "gaps",
        "confidence",
        "risks",
        "llm_assessment",
        "provenance",
        # Added by e1c8f5a20d74 for the retrieval pass this replaces; the model
        # that produced a relevance is now recorded as rerank_model.
        "relevance_model",
    ):
        op.drop_column("job_matches", column)
    op.add_column(
        "job_matches",
        sa.Column("filter_reasons", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("job_matches", sa.Column("score", sa.Float(), nullable=False, server_default="0"))
    op.add_column(
        "job_matches", sa.Column("similarity", sa.Float(), nullable=False, server_default="0")
    )
    # `relevance` already exists (e1c8f5a20d74); only its companions are new.
    op.add_column("job_matches", sa.Column("rerank_position", sa.Integer(), nullable=True))
    op.add_column("job_matches", sa.Column("embedding_model", sa.String(), nullable=True))
    op.add_column("job_matches", sa.Column("rerank_model", sa.String(), nullable=True))
    op.add_column("job_matches", sa.Column("rerank_weight", sa.Float(), nullable=True))
    # Was nullable when a match could exist without one; every match now has a band.
    op.execute("UPDATE job_matches SET recommendation = 'skip' WHERE recommendation IS NULL")
    op.alter_column("job_matches", "recommendation", nullable=False)

    # --- job_source_records / canonical_jobs: nothing is extracted any more ---
    for column in (
        "skills",
        "skills_extracted_by",
        "category",
        "category_confidence",
        "skills_extraction_key",
    ):
        op.drop_column("job_source_records", column)
    op.drop_column("canonical_jobs", "content_hash")
    op.drop_column("canonical_jobs", "content_version")

    # --- scrape_runs: `pages` was recorded and never read ---------------------
    op.drop_column("scrape_runs", "pages")
    op.drop_column("scrape_runs", "updated_count")

    # --- user_preferences: two fields with nothing left to drive -------------
    # acceptable_stack fed the skill matcher; industries_blacklist was never
    # implemented (NormalizedJob has no industry to check against).
    op.drop_column("user_preferences", "acceptable_stack")
    op.drop_column("user_preferences", "industries_blacklist")

    # --- notification_settings: one threshold, not four ----------------------
    op.add_column(
        "notification_settings",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "notification_settings",
        sa.Column("min_score", sa.Float(), nullable=False, server_default="75"),
    )
    op.execute("UPDATE notification_settings SET min_score = immediate_threshold")
    for column in (
        "immediate_threshold",
        "conditional_threshold",
        "digest_threshold",
        "strong_component_threshold",
    ):
        op.drop_column("notification_settings", column)

    # --- new: pipeline config and run history --------------------------------
    op.create_table(
        "pipeline_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("rerank_model", sa.String(), nullable=False),
        sa.Column("scrape_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("scrape_max_jobs_per_run", sa.Integer(), nullable=False),
        sa.Column("retrieval_limit", sa.Integer(), nullable=False),
        sa.Column("rerank_top_k", sa.Integer(), nullable=False),
        sa.Column("rerank_weight", sa.Float(), nullable=False),
        sa.Column("apply_threshold", sa.Float(), nullable=False),
        sa.Column("consider_threshold", sa.Float(), nullable=False),
        sa.Column("job_retention_days", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("pipeline_runs")
    op.drop_table("pipeline_config")

    op.add_column(
        "notification_settings",
        sa.Column("immediate_threshold", sa.Float(), nullable=False, server_default="85"),
    )
    op.add_column(
        "notification_settings",
        sa.Column("conditional_threshold", sa.Float(), nullable=False, server_default="75"),
    )
    op.add_column(
        "notification_settings",
        sa.Column("digest_threshold", sa.Float(), nullable=False, server_default="65"),
    )
    op.add_column(
        "notification_settings",
        sa.Column("strong_component_threshold", sa.Float(), nullable=False, server_default="90"),
    )
    op.drop_column("notification_settings", "min_score")
    op.drop_column("notification_settings", "enabled")

    op.add_column(
        "user_preferences",
        sa.Column("acceptable_stack", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "user_preferences",
        sa.Column("industries_blacklist", postgresql.JSONB(), nullable=False, server_default="[]"),
    )

    op.add_column(
        "scrape_runs", sa.Column("pages", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "scrape_runs", sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0")
    )

    op.add_column("canonical_jobs", sa.Column("content_hash", sa.String(), nullable=True))
    op.add_column(
        "canonical_jobs",
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "job_source_records", sa.Column("skills", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.add_column("job_source_records", sa.Column("skills_extracted_by", sa.String(), nullable=True))
    op.add_column("job_source_records", sa.Column("category", sa.String(), nullable=True))
    op.add_column("job_source_records", sa.Column("category_confidence", sa.Float(), nullable=True))
    op.add_column(
        "job_source_records", sa.Column("skills_extraction_key", sa.String(), nullable=True)
    )

    op.execute("DELETE FROM notification_deliveries")
    op.execute("DELETE FROM notifications")
    op.execute("DELETE FROM job_matches")
    op.alter_column("job_matches", "recommendation", nullable=True)
    for column in (
        "rerank_weight",
        "rerank_model",
        "embedding_model",
        "rerank_position",
        "similarity",
        "score",
        "filter_reasons",
    ):
        op.drop_column("job_matches", column)
    op.add_column(
        "job_matches",
        sa.Column("requirement_match", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "job_matches", sa.Column("practical_fit", sa.Float(), nullable=False, server_default="0")
    )
    op.add_column(
        "job_matches", sa.Column("breakdown", postgresql.JSONB(), nullable=False, server_default="{}")
    )
    op.add_column(
        "job_matches", sa.Column("strengths", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.add_column("job_matches", sa.Column("gaps", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("job_matches", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("job_matches", sa.Column("risks", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("job_matches", sa.Column("llm_assessment", postgresql.JSONB(), nullable=True))
    op.add_column("job_matches", sa.Column("provenance", postgresql.JSONB(), nullable=True))
    op.add_column("job_matches", sa.Column("relevance_model", sa.String(), nullable=True))

    op.execute("DELETE FROM document_embeddings")
    op.drop_constraint(
        "uq_document_embeddings_document_model", "document_embeddings", type_="unique"
    )
    op.drop_column("document_embeddings", "model")
    op.add_column("document_embeddings", sa.Column("section", sa.String(), nullable=False))
    op.add_column("document_embeddings", sa.Column("lane_id", sa.String(), nullable=False))
    op.add_column(
        "document_embeddings",
        sa.Column("document_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_document_embeddings_slot",
        "document_embeddings",
        ["document_type", "document_id", "section", "lane_id"],
    )

    op.create_table(
        "embedding_lanes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="building"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "canonical_job_id", sa.Uuid(), sa.ForeignKey("canonical_jobs.id"), nullable=False
        ),
        sa.Column("status", sa.String(), nullable=False),
    )
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "cv_document_id",
            sa.Uuid(),
            sa.ForeignKey("cv_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("experience_years", sa.Float(), nullable=False),
        sa.Column("roles", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("experience", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("achievements", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("domains", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("ai_experience", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("generated_by", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column(
            "extracted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "candidate_skill_overrides",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("skill_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("years", sa.Float(), nullable=True),
        sa.Column("removed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "skill_key"),
    )
    op.create_table(
        "ai_invocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_ai_invocations_created_at", "ai_invocations", ["created_at"])
