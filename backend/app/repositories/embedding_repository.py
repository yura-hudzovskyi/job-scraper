"""Persistence for document vectors — see docs/pipeline.md.

Two rules this module exists to enforce: a query only ever compares vectors from
one model, and a document is only re-embedded when its text actually changed.

The similarity query is raw SQL because pgvector's `<=>` operator reads more
clearly written out than assembled through ORM expressions. The query vector is
passed as a text literal cast to `vector`, which needs no driver-side type
registration and works identically under asyncpg and psycopg.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.embedding import DocumentEmbeddingModel
from app.repositories.base import rows_affected

JOB = "job"
PROFILE = "profile"


@dataclass(frozen=True)
class Candidate:
    document_id: uuid.UUID
    similarity: float


def _literal(vector: list[float]) -> str:
    """pgvector's text input format. Values are floats produced by the provider,
    so there is nothing to escape."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class EmbeddingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def stored_hashes(
        self, document_type: str, model: str, document_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """What is already indexed for these documents under this model, so a
        batch can skip the ones whose text hasn't moved."""
        if not document_ids:
            return {}
        result = await self._session.execute(
            select(DocumentEmbeddingModel.document_id, DocumentEmbeddingModel.content_hash).where(
                DocumentEmbeddingModel.document_type == document_type,
                DocumentEmbeddingModel.model == model,
                DocumentEmbeddingModel.document_id.in_(document_ids),
            )
        )
        return {document_id: content_hash for document_id, content_hash in result.all()}

    async def save_vector(
        self,
        document_type: str,
        document_id: uuid.UUID,
        model: str,
        content_hash: str,
        vector: list[float],
    ) -> None:
        stmt = (
            insert(DocumentEmbeddingModel)
            .values(
                document_type=document_type,
                document_id=document_id,
                model=model,
                content_hash=content_hash,
                vector=vector,
            )
            .on_conflict_do_update(
                index_elements=[
                    DocumentEmbeddingModel.document_type,
                    DocumentEmbeddingModel.document_id,
                    DocumentEmbeddingModel.model,
                ],
                set_={"content_hash": content_hash, "vector": vector},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_vector(
        self, document_type: str, document_id: uuid.UUID, model: str
    ) -> list[float] | None:
        result = await self._session.execute(
            select(DocumentEmbeddingModel.vector).where(
                DocumentEmbeddingModel.document_type == document_type,
                DocumentEmbeddingModel.document_id == document_id,
                DocumentEmbeddingModel.model == model,
            )
        )
        vector = result.scalar_one_or_none()
        return list(vector) if vector is not None else None

    async def count(self, document_type: str, model: str | None = None) -> int:
        stmt = select(func.count()).select_from(DocumentEmbeddingModel).where(
            DocumentEmbeddingModel.document_type == document_type
        )
        if model is not None:
            stmt = stmt.where(DocumentEmbeddingModel.model == model)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def models_in_use(self) -> list[tuple[str, str, int]]:
        """(document_type, model, rows) for everything stored. Vectors left over
        from a previous model show up here, which is how the System page can say
        the corpus needs re-embedding instead of silently returning nothing."""
        result = await self._session.execute(
            select(
                DocumentEmbeddingModel.document_type,
                DocumentEmbeddingModel.model,
                func.count(),
            ).group_by(DocumentEmbeddingModel.document_type, DocumentEmbeddingModel.model)
        )
        return [(row[0], row[1], int(row[2])) for row in result.all()]

    async def search(
        self, model: str, query_vector: list[float], limit: int
    ) -> list[Candidate]:
        """The most similar job vectors to this query, best first. Cosine
        distance is `<=>`; 1 minus it is the similarity the rest of the app
        talks in.

        The cast is written `CAST(:query AS vector)`, never `:query::vector`.
        SQLAlchemy's `text()` only recognises a bind parameter when it *isn't*
        followed by a colon, so Postgres's `::` cast syntax silently truncates the
        name: `:query::vector` binds a parameter called `quer` and leaves the rest
        as literal SQL, which reaches the driver as a syntax error at `:`.
        """
        sql = text(
            """
            SELECT document_id, 1 - (vector <=> CAST(:query AS vector)) AS similarity
            FROM document_embeddings
            WHERE document_type = :document_type AND model = :model
            ORDER BY vector <=> CAST(:query AS vector)
            LIMIT :limit
            """
        )
        result = await self._session.execute(
            sql,
            {
                "query": _literal(query_vector),
                "document_type": JOB,
                "model": model,
                "limit": limit,
            },
        )
        return [Candidate(document_id=row[0], similarity=float(row[1])) for row in result.all()]

    async def delete_for_documents(
        self, document_type: str, document_ids: list[uuid.UUID]
    ) -> None:
        """Vectors outlive nothing: when a job is purged its vector goes with it,
        or it keeps turning up in searches for a vacancy that no longer exists."""
        if not document_ids:
            return
        await self._session.execute(
            delete(DocumentEmbeddingModel).where(
                DocumentEmbeddingModel.document_type == document_type,
                DocumentEmbeddingModel.document_id.in_(document_ids),
            )
        )
        await self._session.flush()

    async def delete_all(self, document_type: str | None = None) -> int:
        """Used by the System page's reset actions. Returns how many rows went."""
        stmt = delete(DocumentEmbeddingModel)
        if document_type is not None:
            stmt = stmt.where(DocumentEmbeddingModel.document_type == document_type)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return rows_affected(result)
