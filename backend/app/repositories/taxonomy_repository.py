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

    async def concept_ids_by_uri(self, namespace: str, version: str) -> dict[str, uuid.UUID]:
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
            await self._session.execute(update(TaxonomyConceptModel), rows[start : start + BATCH])
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

    async def surface_forms(
        self, namespace: str, version: str
    ) -> list[tuple[uuid.UUID, list[str]]]:
        """Every concept's labels in every imported language, for the alias index.

        One query returning the whole release — 18 000 rows, roughly 15 MB of
        JSONB. That is why the index is cached per version rather than rebuilt
        per document (spec 9.5).
        """
        result = await self._session.execute(
            select(TaxonomyConceptModel.id, TaxonomyConceptModel.labels).where(
                TaxonomyConceptModel.namespace == namespace,
                TaxonomyConceptModel.taxonomy_version == version,
                TaxonomyConceptModel.status == "active",
            )
        )
        return [
            (concept_id, [form for forms in labels.values() for form in forms])
            for concept_id, labels in result.all()
        ]

    # --- internal concepts (spec 9.4) ----------------------------------------

    # The namespace for concepts this system created rather than imported, and
    # the one version it ever has. ESCO ships releases; internal concepts grow
    # one promotion at a time, so a version number would only ever be 1 and
    # pretending otherwise would suggest a release cadence that does not exist.
    INTERNAL_NAMESPACE = "internal"
    INTERNAL_VERSION = "1"

    async def ensure_internal_version(self) -> VersionRecord:
        """The internal namespace's version row, created on first use.

        Lazily rather than in a migration: it matters only once somebody
        promotes a term, and a data migration inserting a row that concepts then
        hang off is a downgrade nobody can write honestly.
        """
        existing = await self.find_version(self.INTERNAL_NAMESPACE, self.INTERNAL_VERSION)
        if existing is not None:
            return existing
        model = TaxonomyVersionModel(
            namespace=self.INTERNAL_NAMESPACE,
            version=self.INTERNAL_VERSION,
            languages=[],
            status="active",
            activated_at=datetime.now(UTC),
        )
        self._session.add(model)
        await self._session.flush()
        return _to_version(model)

    async def internal_concept_by_term(self, normalized_text: str) -> uuid.UUID | None:
        """The concept a term was already promoted into, if it was.

        Promotion is idempotent because of this: `external_id` is derived from
        the normalized term, so promoting the same word twice finds the first
        concept rather than creating a rival with the same label.
        """
        result = await self._session.execute(
            select(TaxonomyConceptModel.id).where(
                TaxonomyConceptModel.namespace == self.INTERNAL_NAMESPACE,
                TaxonomyConceptModel.external_id == self._internal_external_id(normalized_text),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _internal_external_id(normalized_text: str) -> str:
        return f"internal:{normalized_text}"

    async def create_internal_concept(
        self,
        normalized_text: str,
        preferred_label: str,
        forms: list[str],
        concept_type: str = "provisional",
    ) -> uuid.UUID:
        """Add a concept for a term the imported taxonomy does not contain.

        `concept_type` is `provisional` rather than one of ESCO's own types
        (spec 9.4 asks for a provisional type): nobody has decided whether
        "Terraform" is a skill or a knowledge item, and recording a guess as if
        it were the publisher's classification would make the two
        indistinguishable later.

        Labels go under `und` — ISO 639-2 for "undetermined". The term was read
        out of a document whose language we know, but the term itself usually
        has no language: `Terraform` is `Terraform` in every vacancy that names
        it, and filing it under `en` would be a claim nobody checked.
        """
        version = await self.ensure_internal_version()
        existing = await self.internal_concept_by_term(normalized_text)
        if existing is not None:
            return existing

        concept = TaxonomyConceptModel(
            namespace=self.INTERNAL_NAMESPACE,
            external_id=self._internal_external_id(normalized_text),
            taxonomy_version=self.INTERNAL_VERSION,
            concept_type=concept_type,
            preferred_label=preferred_label,
            labels={"und": sorted({form for form in [*forms, preferred_label] if form.strip()})},
            status="active",
        )
        self._session.add(concept)
        await self._session.flush()

        # The count doubles as the linker's cache generation: every process
        # rebuilds its alias index when this changes, which is how a promotion
        # made in the API reaches a worker that has its own cached index.
        model = await self._session.get(TaxonomyVersionModel, version.id)
        if model is not None:
            model.concept_count = model.concept_count + 1
            await self._session.flush()
        return concept.id

    async def retire_internal_concept(self, concept_id: uuid.UUID) -> bool:
        """Take an internal concept out of the index without deleting it.

        `retired` rather than a DELETE because mentions already link to it by
        foreign key, and those rows are the record of what the linker answered
        at the time. `surface_forms` only reads `active`, so a retired concept
        stops being matched on the next index build — which the generation count
        triggers, since it counts active ones.

        Only internal concepts. An imported release is not ours to edit; a
        wrong ESCO concept is a reason to import a different release.
        """
        concept = await self._session.get(TaxonomyConceptModel, concept_id)
        if concept is None or concept.namespace != self.INTERNAL_NAMESPACE:
            return False
        concept.status = "retired"
        await self._session.flush()
        return True

    async def internal_generation(self) -> int:
        """How many internal concepts exist — the alias index's cache key.

        A number rather than a timestamp because it is the thing that actually
        changes the index. Two processes that agree on it are looking at the
        same taxonomy.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(TaxonomyConceptModel)
            .where(
                TaxonomyConceptModel.namespace == self.INTERNAL_NAMESPACE,
                TaxonomyConceptModel.status == "active",
            )
        )
        return int(result.scalar_one())

    async def internal_surface_forms(self) -> list[tuple[uuid.UUID, list[str]]]:
        return await self.surface_forms(self.INTERNAL_NAMESPACE, self.INTERNAL_VERSION)

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
            delete(TaxonomyRelationModel).where(TaxonomyRelationModel.source_concept_id.in_(ids))
        )
        await self._session.execute(
            delete(TaxonomyRelationModel).where(TaxonomyRelationModel.target_concept_id.in_(ids))
        )
        await self._session.execute(
            delete(TaxonomyConceptModel).where(
                TaxonomyConceptModel.namespace == namespace,
                TaxonomyConceptModel.taxonomy_version == version,
            )
        )
        await self._session.flush()
