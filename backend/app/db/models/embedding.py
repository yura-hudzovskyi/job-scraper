"""One vector per document — see docs/pipeline.md.

Deliberately one vector per job and one per user, not per section: a single
document embedded whole is what the search compares, and it is what the System
page can honestly report coverage for ("1,240 of 1,240 vacancies embedded").

`model` is on the row because vectors from different models are not comparable.
Every query filters on it, so pointing the app at a new model doesn't silently
mix vector spaces — the old rows simply stop matching and are re-embedded.

`vector` has no fixed dimension because models differ (1024 for most Voyage
models, more for some). That rules out an ANN index, which is fine at this corpus
size: a few thousand rows is an exact scan in milliseconds. Add IVFFlat/HNSW when
a measurement says so, not before.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class DocumentEmbeddingModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_embeddings"
    __table_args__ = (UniqueConstraint("document_type", "document_id", "model"),)

    # "job" (document_id = canonical job id) or "profile" (document_id = user id).
    # Deliberately not a foreign key: the two live in different tables, and a
    # polymorphic FK would buy nothing the explicit cleanup doesn't already cover.
    document_type: Mapped[str]
    document_id: Mapped[uuid.UUID]
    model: Mapped[str]
    # Hash of the exact text this vector was computed from, so a re-scrape that
    # changed nothing material costs no API call.
    content_hash: Mapped[str]
    vector: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
