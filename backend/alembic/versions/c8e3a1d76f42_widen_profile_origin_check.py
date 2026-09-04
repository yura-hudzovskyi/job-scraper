"""automated profile origins must all name what produced them

Revision ID: c8e3a1d76f42
Revises: b7d2f9a34c15
Create Date: 2026-09-04 13:30:00.000000

Phase 1 wrote this constraint when `neural_extraction` was the only automated
origin, so it named that value directly. Phase 3 adds `structural_extraction`
for the deterministic extractor, which left the constraint's intent — an
automated profile has to say what produced it, or it cannot be reproduced —
with a hole exactly the width of the new value.

Widened rather than replaced: `user_override` and `migration` still legitimately
have no model id, because a person and a backfill are not models.

No data changes. `profile_revisions` is empty until the extractor writes to it.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e3a1d76f42"
down_revision: str | Sequence[str] | None = "b7d2f9a34c15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "origin <> 'neural_extraction' OR extractor_model_id IS NOT NULL"
_NEW = (
    "origin NOT IN ('neural_extraction', 'structural_extraction') "
    "OR extractor_model_id IS NOT NULL"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_profile_revisions_extraction_names_its_model",
        "profile_revisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_profile_revisions_extraction_names_its_model", "profile_revisions", _NEW
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_profile_revisions_extraction_names_its_model",
        "profile_revisions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_profile_revisions_extraction_names_its_model", "profile_revisions", _OLD
    )
