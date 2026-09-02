"""The single row holding PipelineConfig — see app/domain/pipeline_config.py.

One row, fixed primary key 1: this is app-wide configuration, not per-user data,
and a table that can only ever have one row is the simplest thing that survives a
Redis flush and shows up in a database dump. A missing row means "never saved",
which the repository reads as the defaults.
"""

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain import pipeline_config


class PipelineConfigModel(Base):
    __tablename__ = "pipeline_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    embedding_model: Mapped[str] = mapped_column(default=pipeline_config.DEFAULTS.embedding_model)
    rerank_model: Mapped[str] = mapped_column(default=pipeline_config.DEFAULTS.rerank_model)
    scrape_enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    scrape_max_jobs_per_run: Mapped[int] = mapped_column(default=100)
    retrieval_limit: Mapped[int] = mapped_column(default=400)
    rerank_top_k: Mapped[int] = mapped_column(default=60)
    rerank_weight: Mapped[float] = mapped_column(default=0.7)
    apply_threshold: Mapped[float] = mapped_column(default=70.0)
    consider_threshold: Mapped[float] = mapped_column(default=45.0)
    job_retention_days: Mapped[int] = mapped_column(default=18)

    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
