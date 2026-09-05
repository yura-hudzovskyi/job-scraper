"""The taxonomy admin endpoints: what the System page can see and decide.

Called as functions with fake repositories rather than through a TestClient, the
way the rest of this suite works — the behaviour under test is the endpoint's
own, and starting an app to reach it would only add a database.

Spec 9.4 is the whole reason these exist: an unknown mention must not become a
concept on its own, so it is counted and a person decides. Until this queue is
reviewable, "the taxonomy has a gap" is a fact only visible in SQL.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.system import (
    ReviewUnmappedRequest,
    get_taxonomy_status,
    list_unmapped_terms,
    review_unmapped_term,
)
from app.repositories.taxonomy_repository import VersionRecord

USER = uuid.uuid4()


class _FakeTaxonomy:
    def __init__(self, version: VersionRecord | None):
        self._version = version

    async def active_version(self, namespace: str) -> VersionRecord | None:
        return self._version


class _FakeMentions:
    def __init__(self, pending: list[tuple[str, str, int]] | None = None):
        self._pending = pending or []
        self.reviewed: list[tuple[str, str]] = []

    async def count_pending_unmapped(self) -> int:
        return len(self._pending)

    async def pending_unmapped(self, limit: int = 50) -> list[tuple[str, str, int]]:
        ordered = sorted(self._pending, key=lambda row: row[2], reverse=True)
        return ordered[:limit]

    async def review_unmapped(self, normalized_text: str, status: str) -> bool:
        if not any(row[0] == normalized_text for row in self._pending):
            return False
        self.reviewed.append((normalized_text, status))
        return True


def _version() -> VersionRecord:
    return VersionRecord(
        id=uuid.uuid4(),
        namespace="esco",
        version="1.2.1",
        status="active",
        languages=["en"],
        source_checksum="abc123",
        concept_count=18237,
        relation_count=30285,
    )


# --- status ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_imported_taxonomy_reports_null_rather_than_failing() -> None:
    """A fresh install has no release yet. That is a state to render, not an
    error to handle."""
    status = await get_taxonomy_status(USER, _FakeTaxonomy(None), _FakeMentions())  # type: ignore[arg-type]

    assert status is None


@pytest.mark.asyncio
async def test_status_reports_the_release_the_linker_is_matching_against() -> None:
    mentions = _FakeMentions([("kubernetes", "Kubernetes", 12)])

    status = await get_taxonomy_status(USER, _FakeTaxonomy(_version()), mentions)  # type: ignore[arg-type]

    assert status is not None
    assert (status.namespace, status.version, status.status) == ("esco", "1.2.1", "active")
    assert status.concepts == 18237
    assert status.pending_unmapped == 1


@pytest.mark.asyncio
async def test_status_names_the_bytes_it_was_built_from() -> None:
    """Two installs claiming ESCO 1.2.1 can still hold different data. The
    checksum is what makes "same version" checkable rather than assumed."""
    status = await get_taxonomy_status(USER, _FakeTaxonomy(_version()), _FakeMentions())  # type: ignore[arg-type]

    assert status is not None
    assert status.source_checksum == "abc123"


# --- the review queue --------------------------------------------------------


@pytest.mark.asyncio
async def test_unmapped_terms_come_back_commonest_first() -> None:
    """Frequency is the signal (spec 9.4): seen once is probably a typo, seen
    four hundred times is a gap worth a person's attention. A queue in any other
    order buries the gap under the typos."""
    mentions = _FakeMentions(
        [("git", "Git", 3), ("kubernetes", "Kubernetes", 412), ("kuberentes", "Kuberentes", 1)]
    )

    terms = await list_unmapped_terms(50, USER, mentions)  # type: ignore[arg-type]

    assert [term.normalized_text for term in terms] == ["kubernetes", "git", "kuberentes"]
    assert terms[0].occurrences == 412


@pytest.mark.asyncio
async def test_the_queue_page_size_is_capped() -> None:
    """The queue grows with the corpus. An unbounded `limit` in a query string
    turns one request into a full table read."""
    mentions = _FakeMentions([(f"term{i}", f"Term{i}", i) for i in range(600)])

    terms = await list_unmapped_terms(10_000, USER, mentions)  # type: ignore[arg-type]

    assert len(terms) == 500


# --- recording a decision ----------------------------------------------------


@pytest.mark.asyncio
async def test_reviewing_a_term_records_the_decision_that_was_made() -> None:
    mentions = _FakeMentions([("kubernetes", "Kubernetes", 412)])

    response = await review_unmapped_term(
        "kubernetes",
        ReviewUnmappedRequest(status="promoted"),
        USER,
        mentions,  # type: ignore[arg-type]
    )

    assert mentions.reviewed == [("kubernetes", "promoted")]
    assert (response.normalized_text, response.status) == ("kubernetes", "promoted")


@pytest.mark.asyncio
async def test_reviewing_a_term_nobody_reported_is_a_404() -> None:
    """Silently succeeding would let a typo in the URL read as a decision
    recorded — and the term would still be sitting in the queue."""
    mentions = _FakeMentions([("kubernetes", "Kubernetes", 412)])

    with pytest.raises(HTTPException) as raised:
        await review_unmapped_term(
            "kuberentes",
            ReviewUnmappedRequest(status="ignored"),
            USER,
            mentions,  # type: ignore[arg-type]
        )

    assert raised.value.status_code == 404
    assert mentions.reviewed == []


def test_a_decision_must_be_one_of_the_two_the_workflow_defines() -> None:
    """`deleted`, `merged`, anything else — a status the review flow does not
    understand would leave the term neither queued nor acted on."""
    with pytest.raises(ValueError):
        ReviewUnmappedRequest(status="deleted")  # type: ignore[arg-type]
