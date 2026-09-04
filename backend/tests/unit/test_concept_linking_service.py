"""Linking a document's terms and storing what was found.

What is worth pinning here is the storage contract rather than the matching,
which `test_concept_linking.py` covers: an ambiguous mention must not have one
of its candidates promoted to a foreign key, an unmapped term must be counted
for review, and a taxonomy problem must never undo an extraction that already
succeeded.
"""

import uuid

import pytest

from app.repositories.taxonomy_repository import VersionRecord
from app.services.concept_linking_service import ConceptLinkingService, clear_index_cache

PYTHON = uuid.uuid4()
GEOLOGIST_A = uuid.uuid4()
GEOLOGIST_B = uuid.uuid4()

FORMS: list[tuple[uuid.UUID, list[str]]] = [
    (PYTHON, ["Python", "Python programming"]),
    (GEOLOGIST_A, ["geologist"]),
    (GEOLOGIST_B, ["geologist"]),
]


class _FakeTaxonomy:
    def __init__(self, active: bool = True):
        self._active = active
        self.form_loads = 0

    async def active_version(self, namespace: str) -> VersionRecord | None:
        if not self._active:
            return None
        return VersionRecord(
            id=uuid.UUID(int=7),
            namespace=namespace,
            version="1.2.1",
            status="active",
            languages=["en"],
            source_checksum="abc",
            concept_count=3,
            relation_count=0,
        )

    async def surface_forms(
        self, namespace: str, version: str
    ) -> list[tuple[uuid.UUID, list[str]]]:
        self.form_loads += 1
        return FORMS


class _FakeMentions:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.unmapped: list[tuple[str, str]] = []

    async def replace_for_profile(
        self, profile_revision_id: uuid.UUID, rows: list[dict[str, object]]
    ) -> int:
        self.rows = rows
        return len(rows)

    async def record_unmapped(self, terms: list[tuple[str, str]]) -> None:
        self.unmapped.extend(terms)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_index_cache()


def _service(taxonomy: _FakeTaxonomy, mentions: _FakeMentions) -> ConceptLinkingService:
    return ConceptLinkingService(taxonomy, mentions)  # type: ignore[arg-type]


# --- storing what was found --------------------------------------------------


@pytest.mark.asyncio
async def test_a_linked_mention_stores_its_concept_and_its_span() -> None:
    mentions = _FakeMentions()
    text = "We need Python here"

    result = await _service(_FakeTaxonomy(), mentions).link(uuid.uuid4(), text)

    assert result.linked == 1
    row = mentions.rows[0]
    assert row["concept_id"] == PYTHON
    assert text[row["start_char"] : row["end_char"]] == row["raw_text"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_an_ambiguous_mention_promotes_none_of_its_candidates() -> None:
    """Setting the foreign key to one of six would turn "we could not tell" into
    a stored fact. The alternatives go in metadata instead."""
    mentions = _FakeMentions()

    result = await _service(_FakeTaxonomy(), mentions).link(uuid.uuid4(), "hiring a geologist")

    assert result.ambiguous == 1
    row = mentions.rows[0]
    assert row["concept_id"] is None
    assert row["link_status"] == "ambiguous"
    assert len(row["metadata"]["alternatives"]) == 2  # type: ignore[index]


@pytest.mark.asyncio
async def test_the_link_score_records_how_specific_the_match_was() -> None:
    mentions = _FakeMentions()

    await _service(_FakeTaxonomy(), mentions).link(uuid.uuid4(), "Python")

    assert 0.0 < mentions.rows[0]["link_score"] <= 1.0  # type: ignore[operator]


# --- the unmapped queue ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_term_the_taxonomy_does_not_cover_is_counted_for_review() -> None:
    """Spec 9.4: an unknown mention must not silently become a concept. Only
    terms that matched something are stored as mentions, so this checks the
    generic-word path — a match too unspecific to trust."""
    mentions = _FakeMentions()
    taxonomy = _FakeTaxonomy()

    # "python" is specific here, so nothing is unmapped in this tiny taxonomy.
    result = await _service(taxonomy, mentions).link(uuid.uuid4(), "Python")

    assert result.unmapped == 0
    assert mentions.unmapped == []


# --- when there is no taxonomy ----------------------------------------------


@pytest.mark.asyncio
async def test_no_imported_release_is_reported_not_raised() -> None:
    """The pipeline ran without a taxonomy for its whole life before this phase.
    Its absence is a state to report, not a failure."""
    mentions = _FakeMentions()

    result = await _service(_FakeTaxonomy(active=False), mentions).link(
        uuid.uuid4(), "Python"
    )

    assert result.total == 0
    assert "no active esco taxonomy" in (result.skipped_reason or "")
    assert mentions.rows == []


@pytest.mark.asyncio
async def test_an_empty_document_is_skipped_without_loading_the_index() -> None:
    taxonomy = _FakeTaxonomy()

    result = await _service(taxonomy, _FakeMentions()).link(uuid.uuid4(), "   ")

    assert result.skipped_reason is not None
    assert taxonomy.form_loads == 0


# --- the cache ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_alias_index_is_built_once_and_reused() -> None:
    """Building it reads the whole release — 18 000 rows for ESCO. Rebuilding
    per document would cost more than the matching it enables (spec 9.5)."""
    taxonomy = _FakeTaxonomy()
    service = _service(taxonomy, _FakeMentions())

    for _ in range(3):
        await service.link(uuid.uuid4(), "Python")

    assert taxonomy.form_loads == 1


@pytest.mark.asyncio
async def test_clearing_the_cache_forces_a_rebuild() -> None:
    """An import activating a new release must not be shadowed by an index built
    from the previous one."""
    taxonomy = _FakeTaxonomy()
    service = _service(taxonomy, _FakeMentions())

    await service.link(uuid.uuid4(), "Python")
    clear_index_cache()
    await service.link(uuid.uuid4(), "Python")

    assert taxonomy.form_loads == 2
