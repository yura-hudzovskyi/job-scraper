"""Use case: load a pinned taxonomy release into the database.

Three properties, all from spec 9.2 and Phase 4's definition of done:

- **Atomic.** Concepts land under a version whose status is `importing`, which
  the linker ignores. Only after the counts check out does `activate` make it
  visible, and the previously active version becomes `superseded` in the same
  transaction. There is no window where half a taxonomy is matchable.
- **Repeatable.** Re-importing the same file is a no-op, decided by checksum
  rather than by version string — publishers re-issue releases under the same
  number, so "1.2.1" does not identify bytes.
- **Versions coexist.** Nothing is deleted on import. A profile linked under an
  older release keeps pointing at concepts that are still there.

Adding a language is a *fourth* thing this does, and it is deliberately not a
new version: ESCO ships one archive per language with the same concept URIs, so
Ukrainian is more labels on rows that already exist. `import_language` merges
into the version that is already there.
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.integrations.taxonomy import esco
from app.repositories.taxonomy_repository import TaxonomyRepository

logger = logging.getLogger(__name__)


class ReleaseIncomplete(ValueError):
    """A required CSV is missing — a partial download, not a smaller taxonomy."""


@dataclass(frozen=True)
class ImportResult:
    namespace: str
    version: str
    language: str
    concepts: int
    relations: int
    skipped_relations: int = 0
    merged_labels: int = 0
    status: str = "ready"
    skipped_reason: str | None = None

    @property
    def imported(self) -> bool:
        return self.skipped_reason is None


class TaxonomyImportService:
    def __init__(self, repository: TaxonomyRepository):
        self._repository = repository

    async def import_release(
        self,
        directory: Path,
        version: str,
        language: str = "en",
        namespace: str = esco.NAMESPACE,
        archive: Path | None = None,
        activate: bool = True,
    ) -> ImportResult:
        """Import a release directory, or merge a language into one already
        imported.

        `archive` is the zip the directory was extracted from; its checksum is
        what makes a re-import detectable. Without it the import still works, it
        just cannot recognise itself later.
        """
        missing = esco.missing_files(directory, language)
        if missing:
            raise ReleaseIncomplete(
                f"{namespace} {version} ({language}) is missing {', '.join(missing)}"
            )

        source_checksum = esco.checksum(archive) if archive is not None else None
        existing = await self._repository.find_version(namespace, version)

        if existing is not None:
            if language in existing.languages:
                return ImportResult(
                    namespace=namespace,
                    version=version,
                    language=language,
                    concepts=existing.concept_count,
                    relations=existing.relation_count,
                    status=existing.status,
                    skipped_reason=(
                        f"{namespace} {version} already holds {language}; "
                        "nothing to import"
                    ),
                )
            return await self._merge_language(existing.id, namespace, version, directory, language)

        return await self._import_fresh(
            directory, version, language, namespace, source_checksum, activate
        )

    async def _import_fresh(
        self,
        directory: Path,
        version: str,
        language: str,
        namespace: str,
        source_checksum: str | None,
        activate: bool,
    ) -> ImportResult:
        opened = await self._repository.begin_version(
            namespace, version, language, source_checksum
        )
        try:
            concepts = esco.parse_concepts(directory, language)
            relations = esco.parse_relations(directory, language)
            if not concepts:
                raise ReleaseIncomplete(f"{namespace} {version} parsed to zero concepts")

            # Ids are minted here so the relation rows can reference them without
            # reading 18 000 rows back out of the database first.
            concept_ids = {record.external_id: uuid.uuid4() for record in concepts}
            await self._repository.insert_concepts(
                [
                    {
                        "id": concept_ids[record.external_id],
                        "namespace": namespace,
                        "external_id": record.external_id,
                        "taxonomy_version": version,
                        "concept_type": record.concept_type,
                        "preferred_label": record.preferred_label,
                        "labels": record.labels,
                        "description": record.description,
                        "status": "active",
                    }
                    for record in concepts
                ]
            )

            # An edge whose endpoints are not both in this release is dropped and
            # counted. A publisher's dangling reference must not fail an import,
            # but it must not vanish silently either.
            edges, skipped = [], 0
            seen: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
            for relation in relations:
                source = concept_ids.get(relation.source_external_id)
                target = concept_ids.get(relation.target_external_id)
                if source is None or target is None:
                    skipped += 1
                    continue
                key = (source, target, relation.relation_type)
                if key in seen:
                    # The composite primary key would reject a repeat, and ESCO
                    # does repeat edges across its files.
                    skipped += 1
                    continue
                seen.add(key)
                edges.append(
                    {
                        "source_concept_id": source,
                        "target_concept_id": target,
                        "relation_type": relation.relation_type,
                    }
                )
            await self._repository.insert_relations(edges)

            stored = await self._repository.count_concepts(namespace, version)
            if stored != len(concepts):
                raise ReleaseIncomplete(
                    f"expected {len(concepts)} concepts after import, found {stored}"
                )

            await self._repository.finish_version(opened.id, stored, len(edges))
            if activate:
                await self._repository.activate(opened.id, namespace)

            logger.info(
                "imported %s %s (%s): %d concepts, %d relations, %d skipped",
                namespace, version, language, stored, len(edges), skipped,
            )
            return ImportResult(
                namespace=namespace,
                version=version,
                language=language,
                concepts=stored,
                relations=len(edges),
                skipped_relations=skipped,
                status="active" if activate else "ready",
            )
        except Exception as exc:
            await self._repository.fail_version(opened.id, str(exc))
            raise

    async def _merge_language(
        self,
        version_id: uuid.UUID,
        namespace: str,
        version: str,
        directory: Path,
        language: str,
    ) -> ImportResult:
        """Add one language's labels to concepts that already exist.

        Concepts absent from this language's files are left alone rather than
        created: a release's languages cover the same URIs, so a mismatch means
        the archives are from different versions, and inventing a concept from
        one language's file would make the two disagree.
        """
        incoming = esco.parse_concepts(directory, language)
        existing = await self._repository.labels_of(namespace, version)

        updates = []
        for record in incoming:
            current = existing.get(record.external_id)
            if current is None:
                continue
            labels = dict(current["labels"])
            if language in labels:
                continue
            labels[language] = record.labels[language]
            updates.append({"id": current["id"], "labels": labels})

        await self._repository.update_labels(updates)
        await self._repository.add_language(version_id, language)

        logger.info(
            "merged %s into %s %s: %d concepts gained labels",
            language, namespace, version, len(updates),
        )
        return ImportResult(
            namespace=namespace,
            version=version,
            language=language,
            concepts=len(existing),
            relations=0,
            merged_labels=len(updates),
            status="merged",
        )
