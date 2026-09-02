"""One row per pipeline run — what the System page's history table reads.

A run records every step it took with its own counts, so "nothing happened" and
"scraped 40, embedded 40, matched 0 because no CV is uploaded" are visibly
different outcomes. Kept in Postgres rather than Redis precisely so a run's
history survives the "Clear Redis" button next to it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class PipelineRunModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "pipeline_runs"

    # "scrape" | "embed" | "match" | "full" — which entry point started it.
    trigger: Mapped[str]
    # "running" | "succeeded" | "failed"
    status: Mapped[str] = mapped_column(default="running")
    # [{"name": "scrape", "status": "ok", "detail": {...}}, ...] — appended as the
    # run progresses, so a UI polling this sees real progress, not just an end state.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(default=None)

    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
