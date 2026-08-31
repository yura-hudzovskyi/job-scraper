"""make cv_documents deletable without orphaning candidate_profiles

Revision ID: a7c1e9f4b2d6
Revises: f2a6c1d0b3e4
Create Date: 2026-08-31 14:00:00.000000

Hand-written and verified the same way as the prior migrations. Users can now
delete an uploaded CV (see DELETE /api/cv/{cv_id}) — candidate_profiles.cv_document_id
was NOT NULL with a plain (RESTRICT) FK, which would have made every CV a
CvDocument was ever analyzed from permanently undeletable. A CandidateProfile is
already a full point-in-time snapshot of everything extracted from the CV (skills,
experience, roles, ...) — it doesn't need the raw CV text to keep meaning — so
ON DELETE SET NULL is the right behavior: deleting the source CV never deletes or
breaks the profiles derived from it, it just severs the now-meaningless backref.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c1e9f4b2d6"
down_revision: str | Sequence[str] | None = "f2a6c1d0b3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("candidate_profiles", "cv_document_id", nullable=True)
    op.drop_constraint(
        "candidate_profiles_cv_document_id_fkey", "candidate_profiles", type_="foreignkey"
    )
    op.create_foreign_key(
        "candidate_profiles_cv_document_id_fkey",
        "candidate_profiles",
        "cv_documents",
        ["cv_document_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "candidate_profiles_cv_document_id_fkey", "candidate_profiles", type_="foreignkey"
    )
    op.create_foreign_key(
        "candidate_profiles_cv_document_id_fkey",
        "candidate_profiles",
        "cv_documents",
        ["cv_document_id"],
        ["id"],
    )
    op.alter_column("candidate_profiles", "cv_document_id", nullable=False)
