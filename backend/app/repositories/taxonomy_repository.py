"""Persistence for imported taxonomy releases.

Bulk-oriented on purpose: an ESCO import is 18 000 concepts and 30 000 edges,
and doing that a row at a time turns a two-second operation into a two-minute
one. Everything here takes lists.

Concept ids are generated client-side rather than read back with RETURNING,
because the relation insert needs a URI-to-id map for all 18 000 concepts before
it can write a single edge — and building that map from the rows we are about to
insert is free, while reading it back is another round trip over the same data.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.taxonomy import (
    TaxonomyConceptModel,
    TaxonomyRelationModel,
    TaxonomyVersionModel,
)

# Postgres caps a statement at 65535 bound parameters. Concepts bind 8 columns
# each, so 4000 rows is comfortably inside it and still one round trip per batch.
BATCH = 4000


@dataclass(frozen=True)
class VersionRecord:
    id: uuid.UUID
    namespace: str
    version: str
    status: str
    languages: list[str]
    source_checksum: str | None
    concept_count: int
    relation_count: int


def _to_version(model: TaxonomyVersionModel) -> VersionRecord:
    return VersionRecord(
        id=model.id,
        namespace=model.namespace,
        version=model.version,
        status=model.status,
        languages=list(model.languages),
        source_checksum=model.source_checksum,
        concept_count=model.concept_count,
        relation_count=model.relation_count,
    )


class TaxonomyRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- versions ------------------------------------------------------------

    async def find_version(self, namespace: str, version: str) -> VersionRecord | None:
        result = await self._session.execute(
            select(TaxonomyVersionModel).where(
                TaxonomyVersionModel.namespace == namespace,
                TaxonomyVersionModel.version == version,
            )
        )
        model = result.scalar_one_or_none()
        return _to_version(model) if model is not None else None

    async def active_version(self, namespace: str) -> VersionRecord | None:
        """What the linker should be matching against right now."""
        result = await self._session.execute(
            select(TaxonomyVersionModel).where(
                TaxonomyVersionModel.namespace == namespace,
                TaxonomyVersionModel.status == "active",
            )
        )
        model = result.scalar_one_or_none()
        return _to_version(model) if model is not None else None

    async def begin_version(
        self, namespace: str, version: str, language: str, source_checksum: str | None
    ) -> VersionRecord:
        """Open a version in `importing`, which nothing reads.

        The status is what makes the import atomic: concepts land under a version
        the linker ignores, and only `activate` makes them visible.
        """
        model = TaxonomyVersionModel(
            namespace=namespace,
            version=version,
            languages=[language],
            source_checksum=source_checksum,
            status="importing",
        )
        self._session.add(model)
        await self._session.flush()
        return _to_version(model)

    async def finish_version(
        self, version_id: uuid.UUID, concept_count: int, relation_count: int
    ) -> None:
        await self._session.execute(
            update(TaxonomyVersionModel)
            .where(TaxonomyVersionModel.id == version_id)
            .values(status="ready", concept_count=concept_count, relation_count=relation_count)
        )
        await self._session.flush()

    async def fail_version(self, version_id: uuid.UUID, detail: str) -> None:
        """Leave a broken import visible rather than deleting it — a version that
        vanished looks like it was never attempted."""
        await self._session.execute(
            update(TaxonomyVersionModel)
            .where(TaxonomyVersionModel.id == version_id)
            .values(status="failed", failure_detail=detail[:1000])
        )
        await self._session.flush()

    async def activate(self, version_id: uuid.UUID, namespace: str) -> None:
        """Make this version the one the linker uses, in one transaction.

        The previously active version becomes `superseded` rather than being
        deleted: profiles linked under it still point at its concepts, and spec
        9.2 step 7 wants the previous version kept for reproducibility.
        """
        await self._session.execute(
            update(TaxonomyVersionModel)
            .where(
                TaxonomyVersionModel.namespace == namespace,
                TaxonomyVersionModel.status == "active",
            )
            .values(status="superseded")
        )
        await self._session.execute(
            update(TaxonomyVersionModel)
            .where(TaxonomyVersionModel.id == version_id)
            .values(status="active", activated_at=datetime.now(UTC))
        )
        await self._session.flush()

    async def add_language(self, version_id: uuid.UUID, language: str) -> None:
        model = await self._session.get(TaxonomyVersionModel, version_id)
        if model is None or language in model.languages:
            return
        model.languages = [*model.languages, language]
        await self._session.flush()

    # --- concepts and relations ----------------------------------------------

    async def insert_concepts(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        for start in range(0, len(rows), BATCH):
            await self._session.execute(insert(TaxonomyConceptModel), rows[start : start + BATCH])
        await self._session.flush()

    async def insert_relations(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        for start in range(0, len(rows), BATCH):
            await self._session.execute(insert(TaxonomyRelationModel), rows[start : start + BATCH])
        await self._session.flush()

    async def concept_ids_by_uri(
        self, namespace: str, version: str
    ) -> dict[str, uuid.UUID]:
        """The URI-to-id map a relation insert or a language merge needs."""
        result = await self._session.execute(
            select(TaxonomyConceptModel.external_id, TaxonomyConceptModel.id).where(
                TaxonomyConceptModel.namespace == namespace,
                TaxonomyConceptModel.taxonomy_version == version,
            )
        )
        return {external_id: concept_id for external_id, concept_id in result.all()}

    async def update_labels(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-update `labels` by primary key — how a second language lands."""
        for start in range(0, len(rows), BATCH):
            await self._session.execute(
                update(TaxonomyConceptModel), rows[start : start + BATCH]
            )
        await self._session.flush()

    async def labels_of(self, namespace: str, version: str) -> dict[str, dict[str, Any]]:
        """Existing label maps, keyed by URI — the left side of a language merge."""
        result = await self._session.execute(
            select(
                TaxonomyConceptModel.external_id,
                TaxonomyConceptModel.id,
                TaxonomyConceptModel.labels,
            ).where(
                TaxonomyConceptModel.namespace == namespace,
                TaxonomyConceptModel.taxonomy_version == version,
            )
        )
        return {
            external_id: {"id": concept_id, "labels": labels}
            for external_id, concept_id, labels in result.all()
        }

    async def count_concepts(self, namespace: str, version: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(TaxonomyConceptModel)
            .where(
                TaxonomyConceptModel.namespace == namespace,
                TaxonomyConceptModel.taxonomy_version == version,
            )
        )
        return int(result.scalar_one())

    async def delete_version_data(self, namespace: str, version: str) -> None:
        """Remove a version's concepts and their edges, for retrying a failed
        import. Relations go first — they hold the foreign keys."""
        ids = select(TaxonomyConceptModel.id).where(
            TaxonomyConceptModel.namespace == namespace,
            TaxonomyConceptModel.taxonomy_version == version,
        )
        await self._session.execute(
            delete(TaxonomyRelationModel).where(
                TaxonomyRelationModel.source_concept_id.in_(ids)
            )
        )
        await self._session.execute(
            delete(TaxonomyRelationModel).where(
                TaxonomyRelationModel.target_concept_id.in_(ids)
            )
        )
        await self._session.execute(
            delete(TaxonomyConceptModel).where(
                TaxonomyConceptModel.namespace == namespace,
                TaxonomyConceptModel.taxonomy_version == version,
            )
        )
        await self._session.flush()
