"""Importing a taxonomy release.

The properties worth pinning are the ones that only show up when something goes
wrong or happens twice: a half-written version must never become visible, a
re-import must do nothing, and adding a language must not create a second copy
of the taxonomy.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.repositories.taxonomy_repository import VersionRecord
from app.services.taxonomy_import_service import (
    ReleaseIncomplete,
    TaxonomyImportService,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "esco"


class _FakeRepository:
    """Records the order of operations, because the order is what makes the
    import atomic — concepts before counts, counts before activation."""

    def __init__(self, existing: VersionRecord | None = None):
        self.existing = existing
        self.calls: list[str] = []
        self.concepts: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.label_updates: list[dict[str, Any]] = []
        self.languages_added: list[str] = []
        self.failed_with: str | None = None
        self.activated = False
        self.stored_labels: dict[str, dict[str, Any]] = {}

    async def find_version(self, namespace: str, version: str) -> VersionRecord | None:
        return self.existing

    async def begin_version(
        self, namespace: str, version: str, language: str, source_checksum: str | None
    ) -> VersionRecord:
        self.calls.append("begin")
        return VersionRecord(
            id=uuid.uuid4(),
            namespace=namespace,
            version=version,
            status="importing",
            languages=[language],
            source_checksum=source_checksum,
            concept_count=0,
            relation_count=0,
        )

    async def insert_concepts(self, rows: list[dict[str, Any]]) -> None:
        self.calls.append("concepts")
        self.concepts = rows

    async def insert_relations(self, rows: list[dict[str, Any]]) -> None:
        self.calls.append("relations")
        self.relations = rows

    async def count_concepts(self, namespace: str, version: str) -> int:
        self.calls.append("count")
        return len(self.concepts)

    async def finish_version(
        self, version_id: uuid.UUID, concept_count: int, relation_count: int
    ) -> None:
        self.calls.append("finish")

    async def activate(self, version_id: uuid.UUID, namespace: str) -> None:
        self.calls.append("activate")
        self.activated = True

    async def fail_version(self, version_id: uuid.UUID, detail: str) -> None:
        self.calls.append("fail")
        self.failed_with = detail

    async def labels_of(self, namespace: str, version: str) -> dict[str, dict[str, Any]]:
        return self.stored_labels

    async def update_labels(self, rows: list[dict[str, Any]]) -> None:
        self.calls.append("update_labels")
        self.label_updates = rows

    async def add_language(self, version_id: uuid.UUID, language: str) -> None:
        self.calls.append("add_language")
        self.languages_added.append(language)


def _service(repository: _FakeRepository) -> TaxonomyImportService:
    return TaxonomyImportService(repository)  # type: ignore[arg-type]


# --- a fresh import ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_release_imports_its_concepts_and_edges() -> None:
    repository = _FakeRepository()

    result = await _service(repository).import_release(FIXTURES, version="1.2.1")

    assert result.imported is True
    assert result.concepts == len(repository.concepts)
    assert result.concepts > 0
    assert result.relations > 0


@pytest.mark.asyncio
async def test_nothing_is_visible_until_the_counts_are_checked() -> None:
    """The order is the atomicity: a version only becomes active after its
    concepts are in and counted."""
    repository = _FakeRepository()

    await _service(repository).import_release(FIXTURES, version="1.2.1")

    assert repository.calls == [
        "begin",
        "concepts",
        "relations",
        "count",
        "finish",
        "activate",
    ]


@pytest.mark.asyncio
async def test_an_import_can_be_staged_without_being_activated() -> None:
    """Useful for loading a release before a switchover — 9.2 step 6 wants
    activation to be its own step."""
    repository = _FakeRepository()

    result = await _service(repository).import_release(
        FIXTURES, version="1.2.1", activate=False
    )

    assert repository.activated is False
    assert result.status == "ready"


@pytest.mark.asyncio
async def test_every_concept_carries_the_version_that_produced_it() -> None:
    repository = _FakeRepository()

    await _service(repository).import_release(FIXTURES, version="1.2.1")

    assert {row["taxonomy_version"] for row in repository.concepts} == {"1.2.1"}


# --- what it refuses ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_partial_download_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    repository = _FakeRepository()

    with pytest.raises(ReleaseIncomplete, match="missing"):
        await _service(repository).import_release(tmp_path, version="1.2.1")

    assert repository.calls == []


@pytest.mark.asyncio
async def test_a_failure_mid_import_marks_the_version_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken import must stay visible as broken. A version that vanished
    looks like it was never attempted."""
    repository = _FakeRepository()

    async def explode(rows: list[dict[str, Any]]) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(repository, "insert_concepts", explode)

    with pytest.raises(RuntimeError):
        await _service(repository).import_release(FIXTURES, version="1.2.1")

    assert repository.failed_with == "disk full"
    assert repository.activated is False


@pytest.mark.asyncio
async def test_a_count_mismatch_fails_the_import_rather_than_activating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If fewer rows landed than were parsed, something silently dropped them,
    and a taxonomy missing concepts is worse than no taxonomy."""
    repository = _FakeRepository()

    async def undercount(namespace: str, version: str) -> int:
        return 1

    monkeypatch.setattr(repository, "count_concepts", undercount)

    with pytest.raises(ReleaseIncomplete, match="expected"):
        await _service(repository).import_release(FIXTURES, version="1.2.1")

    assert repository.activated is False


# --- edges the publisher got wrong -------------------------------------------


@pytest.mark.asyncio
async def test_an_edge_pointing_outside_the_release_is_dropped_and_counted() -> None:
    """The fixture has one broader relation whose target is not in the release.
    A publisher's dangling reference must not fail an import, and must not
    disappear without a number either."""
    repository = _FakeRepository()

    result = await _service(repository).import_release(FIXTURES, version="1.2.1")

    assert result.skipped_relations >= 1


@pytest.mark.asyncio
async def test_no_duplicate_edge_reaches_the_composite_primary_key() -> None:
    repository = _FakeRepository()

    await _service(repository).import_release(FIXTURES, version="1.2.1")

    keys = [
        (row["source_concept_id"], row["target_concept_id"], row["relation_type"])
        for row in repository.relations
    ]
    assert len(keys) == len(set(keys))


# --- re-import and languages -------------------------------------------------


def _imported(languages: list[str]) -> VersionRecord:
    return VersionRecord(
        id=uuid.uuid4(),
        namespace="esco",
        version="1.2.1",
        status="active",
        languages=languages,
        source_checksum="abc",
        concept_count=18237,
        relation_count=30285,
    )


@pytest.mark.asyncio
async def test_re_importing_a_language_already_present_does_nothing() -> None:
    repository = _FakeRepository(existing=_imported(["en"]))

    result = await _service(repository).import_release(FIXTURES, version="1.2.1", language="en")

    assert result.imported is False
    assert "already holds en" in (result.skipped_reason or "")
    assert repository.calls == []


@pytest.mark.asyncio
async def test_a_new_language_merges_into_the_existing_version() -> None:
    """ESCO ships one archive per language with the same concept URIs, so
    Ukrainian is more labels on rows that already exist — not a second
    taxonomy."""
    repository = _FakeRepository(existing=_imported(["uk"]))
    repository.stored_labels = {
        "http://data.europa.eu/esco/skill/aaa": {
            "id": uuid.uuid4(),
            "labels": {"uk": ["керувати музичним персоналом"]},
        }
    }

    result = await _service(repository).import_release(
        FIXTURES, version="1.2.1", language="en"
    )

    assert result.merged_labels == 1
    assert repository.languages_added == ["en"]
    assert repository.concepts == [], "a merge must not insert concepts"
    merged = repository.label_updates[0]["labels"]
    assert set(merged) == {"uk", "en"}


@pytest.mark.asyncio
async def test_a_language_merge_ignores_concepts_the_version_does_not_have() -> None:
    """A URI present in one language's files but not in the imported version
    means the archives are from different releases. Inventing the concept would
    make the two disagree."""
    repository = _FakeRepository(existing=_imported(["uk"]))
    repository.stored_labels = {}

    result = await _service(repository).import_release(
        FIXTURES, version="1.2.1", language="en"
    )

    assert result.merged_labels == 0
    assert repository.label_updates == []
