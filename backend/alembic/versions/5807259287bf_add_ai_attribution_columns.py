"""add AI attribution columns

Revision ID: 5807259287bf
Revises: 08ef1cce98d5
Create Date: 2026-08-30 00:00:00.000000

Hand-written and verified the same way as the prior migrations. Records which
LLM produced each AI-generated result, so the UI never presents a Gemini-fallback
Ollama result as if it were the primary provider without saying so — see
app/integrations/ai/llm/base.py::LLMResult and app/services/cv_service.py /
app/services/job_skill_extraction_service.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5807259287bf"
down_revision: str | Sequence[str] | None = "08ef1cce98d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles", sa.Column("generated_by", sa.String(), nullable=True)
    )
    op.add_column(
        "job_source_records", sa.Column("skills_extracted_by", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("job_source_records", "skills_extracted_by")
    op.drop_column("candidate_profiles", "generated_by")
