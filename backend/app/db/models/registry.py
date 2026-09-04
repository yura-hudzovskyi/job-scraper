"""ORM table for the model registry — spec 7.4.

Two kinds of row, and the difference is load-bearing (spec 3.5.1):

- `deployment = "api"` — a retrieval model called over HTTPS (Voyage today). The
  provider owns its versioning, so `revision`/`license`/`runtime_backend` are
  empty and the *active* one is a `pipeline_config` setting edited on the System
  page, exactly as it is today.
- `deployment = "self_hosted"` — an understanding model loaded by `ml-service`
  (GLiNER2, the concept linker). Its weights are ours to pin, so `revision` is
  required: a model that can't be re-downloaded byte-identically can't reproduce
  an old extraction.

This table records what was benchmarked and activated, and when. It does not
drive runtime behavior for retrieval — that stays in `pipeline_config`, so this
is history and provenance, not a second source of truth.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class ModelRegistryModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        CheckConstraint(
            "deployment <> 'self_hosted' OR revision IS NOT NULL",
            name="ck_model_registry_self_hosted_is_pinned",
        ),
    )

    # "embedding" | "rerank" | "extraction" | "concept_linking"
    purpose: Mapped[str]
    # "api" | "self_hosted"
    deployment: Mapped[str]
    # "voyage", "gliner2", ...
    provider: Mapped[str]
    # "voyage-4-large", "rerank-3", "fastino/gliner2.5-multi-v1", ...
    model_id: Mapped[str]
    # Pinned commit/tag. Required for self_hosted (check constraint above);
    # for api rows the provider's model name already is the version.
    revision: Mapped[str | None] = mapped_column(default=None)
    license: Mapped[str | None] = mapped_column(default=None)
    runtime_backend: Mapped[str | None] = mapped_column(default=None)

    dimensions: Mapped[int | None] = mapped_column(default=None)
    max_tokens: Mapped[int | None] = mapped_column(default=None)

    # "active" | "deprecated"
    status: Mapped[str] = mapped_column(default="active", server_default="active")
    notes: Mapped[str | None] = mapped_column(default=None)
    activated_at: Mapped[datetime] = mapped_column(server_default=func.now())
