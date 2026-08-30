"""add scrape_runs category

Revision ID: c7940d19c304
Revises: 3087399dee38
Create Date: 2026-08-30 00:20:00.000000

Hand-written and verified the same way as the prior migrations. ScrapeRunModel was
defined but never written to before this — this column, plus actually starting to
insert rows (see app/workers/tasks/scrape.py), is what the category-rotation scrape
schedule reads to pick "whichever category hasn't been scraped in the longest time"
per source. See app/repositories/job_repository.py::get_least_recently_scraped_category.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7940d19c304"
down_revision: str | Sequence[str] | None = "3087399dee38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scrape_runs", sa.Column("category", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("scrape_runs", "category")
