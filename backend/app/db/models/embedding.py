"""ORM tables for section vectors and the lanes they belong to — see
docs/ai-pipeline-v3.md (C2, 7).

A "lane" is one embedding model's vector space: its own model, its own dimension,
its own vectors. Vectors from different models are not comparable, so every
stored vector names its lane and every query runs inside exactly one — the bug
this table shape exists to make impossible is a BGE query vector meeting a Voyage
index.

`document_embeddings.vector` is declared without a fixed dimension because lanes
differ (384 locally, 1024 for BGE-M3/Voyage). That rules out an ANN index, which
is fine and deliberate at this corpus size: a few thousand jobs is an exact
distance scan in milliseconds, and pgvector's own guidance is to add IVFFlat/HNSW
when measurements say so, not before (docs/ai-pipeline-v3.md, C5). When that day
comes, each lane gets its own dimensioned partial index.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class EmbeddingLaneModel(Base):
    """One embedding model in use, keyed by its own id ("bge-m3:1024:v1") rather
    than a surrogate: the id is what every vector row carries, and it is meant to
    be readable in a debug view."""

    __tablename__ = "embedding_lanes"

    id: Mapped[str] = mapped_column(primary_key=True)
    provider: Mapped[str]
    model: Mapped[str]
    dimension: Mapped[int]
    # "quality" (best available model) or "durable" (always-available fallback).
    # Retrieval prefers quality when it is ready and falls back to durable, never
    # mixing the two in one query.
    role: Mapped[str]
    # building -> ready -> degraded/retired. Only a ready lane serves queries.
    state: Mapped[str] = mapped_column(default="building", server_default="building")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(default=None)


class DocumentEmbeddingModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_embeddings"
    __table_args__ = (
        # One vector per (document, section, lane). The plan keys this on the
        # content hash too; keeping the hash as a column instead means a changed
        # section *replaces* its vector rather than accumulating dead ones, and
        # an unchanged section is recognised without a second row.
        UniqueConstraint("document_type", "document_id", "section", "lane_id"),
    )

    # "job" or "profile" — deliberately not a foreign key: the two live in
    # different tables, and a polymorphic FK would buy nothing that
    # JobRetentionService's explicit cleanup doesn't already cover.
    document_type: Mapped[str]
    document_id: Mapped[uuid.UUID]
    document_version: Mapped[int]
    section: Mapped[str]
    lane_id: Mapped[str]
    # Hash of the section text this vector was computed from, so re-indexing skips
    # sections that haven't changed.
    content_hash: Mapped[str]
    vector: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
