"""Persistence for section vectors and the lanes they belong to.

Two rules this module exists to enforce, both from docs/ai-pipeline-v3.md (C2):
every query runs inside exactly one lane, and a section is only re-embedded when
its text actually changed.

The similarity query is raw SQL rather than ORM expressions because pgvector's
`<=>` operator, the per-section join of query vectors, and the weighted sum are
all clearer written out than assembled. Query vectors are passed as text literals
cast to `vector`, which needs no driver-side type registration and works
identically under asyncpg and psycopg.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.embedding import DocumentEmbeddingModel, EmbeddingLaneModel

JOB = "job"
PROFILE = "profile"


@dataclass(frozen=True)
class SectionVector:
    section: str
    content_hash: str
    vector: list[float]


@dataclass(frozen=True)
class SectionQuery:
    """One section of the query document, with how much it counts toward the
    combined score."""

    section: str
    weight: float
    vector: list[float]


@dataclass(frozen=True)
class Candidate:
    document_id: uuid.UUID
    score: float


@dataclass(frozen=True)
class EmbeddingLane:
    id: str
    provider: str
    model: str
    dimension: int
    role: str
    state: str


def _literal(vector: list[float]) -> str:
    """pgvector's text input format. Values are floats produced by our own
    providers, so there is nothing to escape."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class EmbeddingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- lanes ---

    async def upsert_lane(self, lane: EmbeddingLane) -> None:
        stmt = (
            insert(EmbeddingLaneModel)
            .values(
                id=lane.id,
                provider=lane.provider,
                model=lane.model,
                dimension=lane.dimension,
                role=lane.role,
                state=lane.state,
            )
            # `state` is deliberately not in the update set: it belongs to the
            # readiness check (EmbeddingIndexingService.refresh_lane_readiness),
            # and re-registering a lane on every write would knock a ready one
            # back to "building" forever.
            .on_conflict_do_update(
                index_elements=[EmbeddingLaneModel.id],
                set_={"role": lane.role, "dimension": lane.dimension},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_lanes(self) -> list[EmbeddingLane]:
        result = await self._session.execute(select(EmbeddingLaneModel))
        return [
            EmbeddingLane(
                id=model.id,
                provider=model.provider,
                model=model.model,
                dimension=model.dimension,
                role=model.role,
                state=model.state,
            )
            for model in result.scalars()
        ]

    async def set_lane_state(self, lane_id: str, state: str) -> None:
        await self._session.execute(
            EmbeddingLaneModel.__table__.update()
            .where(EmbeddingLaneModel.id == lane_id)
            .values(state=state)
        )
        await self._session.flush()

    async def documents_with_vectors(self, lane_id: str, document_type: str) -> int:
        """How many documents this lane has any vector for — the numerator of its
        coverage. Counting live rather than storing a percentage keeps it from
        going stale the moment a job is scraped or purged."""
        result = await self._session.execute(
            text(
                """
                SELECT COUNT(DISTINCT document_id) FROM document_embeddings
                WHERE lane_id = :lane_id AND document_type = :document_type
                """
            ),
            {"lane_id": lane_id, "document_type": document_type},
        )
        return int(result.scalar_one())

    # --- vectors ---

    async def stored_hashes(
        self, document_type: str, document_id: uuid.UUID, lane_id: str
    ) -> dict[str, str]:
        """What this lane already holds for a document, so indexing can skip the
        sections whose text hasn't moved."""
        result = await self._session.execute(
            select(DocumentEmbeddingModel.section, DocumentEmbeddingModel.content_hash).where(
                DocumentEmbeddingModel.document_type == document_type,
                DocumentEmbeddingModel.document_id == document_id,
                DocumentEmbeddingModel.lane_id == lane_id,
            )
        )
        return {section: content_hash for section, content_hash in result.all()}

    async def save_vectors(
        self,
        document_type: str,
        document_id: uuid.UUID,
        document_version: int,
        lane_id: str,
        vectors: list[SectionVector],
    ) -> int:
        if not vectors:
            return 0
        for vector in vectors:
            stmt = (
                insert(DocumentEmbeddingModel)
                .values(
                    document_type=document_type,
                    document_id=document_id,
                    document_version=document_version,
                    section=vector.section,
                    lane_id=lane_id,
                    content_hash=vector.content_hash,
                    vector=vector.vector,
                )
                .on_conflict_do_update(
                    constraint="uq_document_embeddings_slot",
                    set_={
                        "document_version": document_version,
                        "content_hash": vector.content_hash,
                        "vector": vector.vector,
                    },
                )
            )
            await self._session.execute(stmt)
        await self._session.flush()
        return len(vectors)

    async def delete_for_documents(
        self, document_type: str, document_ids: list[uuid.UUID]
    ) -> None:
        """Vectors outlive nothing: when a job is purged its vectors go with it,
        or they'd keep turning up in retrieval for a vacancy that no longer
        exists."""
        if not document_ids:
            return
        await self._session.execute(
            delete(DocumentEmbeddingModel).where(
                DocumentEmbeddingModel.document_type == document_type,
                DocumentEmbeddingModel.document_id.in_(document_ids),
            )
        )
        await self._session.flush()

    # --- retrieval ---

    async def search(
        self,
        lane_id: str,
        document_type: str,
        queries: list[SectionQuery],
        limit: int,
        candidate_ids: list[uuid.UUID] | None = None,
    ) -> list[Candidate]:
        """Weighted section similarity inside one lane. `candidate_ids` restricts
        the search to documents that already passed the structured filters, so
        the vector scan never has to re-derive eligibility.

        Sections the query doesn't have simply don't contribute, and a document
        missing a section it does have scores lower rather than being excluded —
        a posting with no explicit requirements is still a candidate.
        """
        if not queries:
            return []

        values = ", ".join(
            f"(:section_{index}, :weight_{index}, :vector_{index}::vector)"
            for index in range(len(queries))
        )
        params: dict[str, object] = {
            "lane_id": lane_id,
            "document_type": document_type,
            "limit": limit,
        }
        for index, query in enumerate(queries):
            params[f"section_{index}"] = query.section
            params[f"weight_{index}"] = query.weight
            params[f"vector_{index}"] = _literal(query.vector)

        restriction = ""
        if candidate_ids is not None:
            if not candidate_ids:
                return []
            restriction = "AND e.document_id = ANY(:candidate_ids)"
            params["candidate_ids"] = candidate_ids

        sql = text(
            f"""
            WITH q(section, weight, embedding) AS (VALUES {values})
            SELECT e.document_id, SUM(q.weight * (1 - (e.vector <=> q.embedding))) AS score
            FROM document_embeddings e
            JOIN q ON q.section = e.section
            WHERE e.lane_id = :lane_id
              AND e.document_type = :document_type
              {restriction}
            GROUP BY e.document_id
            ORDER BY score DESC
            LIMIT :limit
            """
        )
        result = await self._session.execute(sql, params)
        return [Candidate(document_id=row[0], score=float(row[1])) for row in result.all()]
