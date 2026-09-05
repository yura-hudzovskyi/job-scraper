"""The taxonomy admin endpoints: what the System page can see and decide.

Called as functions with fake repositories rather than through a TestClient, the
way the rest of this suite works — the behaviour under test is the endpoint's
own, and starting an app to reach it would only add a database.

Spec 9.4 is the whole reason these exist: an unknown mention must not become a
concept on its own, so it is counted and a person decides. Until this queue is
reviewable, "the taxonomy has a gap" is a fact only visible in SQL.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes.system import (
    BulkReviewRequest,
    ReviewUnmappedRequest,
    get_taxonomy_status,
    list_unmapped_terms,
    review_unmapped_term,
    review_unmapped_terms,
)
from app.repositories.taxonomy_repository import VersionRecord
from app.services.concept_promotion_service import ConceptPromotionService

USER = uuid.uuid4()


class _FakeTaxonomy:
    def __init__(self, version: VersionRecord | None):
        self._version = version

    async def active_version(self, namespace: str) -> VersionRecord | None:
        return self._version


class _FakeMentions:
    def __init__(self, pending: list[tuple[str, str, int]] | None = None):
        self._pending = pending or []
        self.reviewed: list[tuple[str, str, uuid.UUID | None]] = []
        self.concept_of: dict[str, uuid.UUID] = {}

    async def count_pending_unmapped(self) -> int:
        return len(self._pending)

    async def pending_unmapped(
        self, limit: int = 50, min_occurrences: int = 1
    ) -> list[tuple[str, str, int]]:
        kept = [row for row in self._pending if row[2] >= min_occurrences]
        return sorted(kept, key=lambda row: row[2], reverse=True)[:limit]

    async def get_unmapped(self, normalized_text: str) -> object | None:
        for normalized, sample, _ in self._pending:
            if normalized == normalized_text:
                return SimpleNamespace(
                    normalized_text=normalized,
                    sample_raw_text=sample,
                    promoted_concept_id=self.concept_of.get(normalized),
                )
        return None

    async def review_unmapped(
        self, normalized_text: str, status: str, concept_id: uuid.UUID | None = None
    ) -> bool:
        if not any(row[0] == normalized_text for row in self._pending):
            return False
        self.reviewed.append((normalized_text, status, concept_id))
        if concept_id is None:
            self.concept_of.pop(normalized_text, None)
        else:
            self.concept_of[normalized_text] = concept_id
        return True


class _FakeTaxonomyWrites:
    """Just enough taxonomy to promote a term into a concept."""

    def __init__(self, existing: dict[str, uuid.UUID] | None = None):
        self.concepts: dict[str, uuid.UUID] = existing or {}
        self.created: list[tuple[str, str, list[str]]] = []
        self.retired: list[uuid.UUID] = []

    async def internal_concept_by_term(self, normalized_text: str) -> uuid.UUID | None:
        return self.concepts.get(normalized_text)

    async def retire_internal_concept(self, concept_id: uuid.UUID) -> bool:
        self.retired.append(concept_id)
        return True

    async def create_internal_concept(
        self,
        normalized_text: str,
        preferred_label: str,
        forms: list[str],
        concept_type: str = "provisional",
    ) -> uuid.UUID:
        self.created.append((normalized_text, preferred_label, forms))
        concept_id = uuid.uuid4()
        self.concepts[normalized_text] = concept_id
        return concept_id


def _promotion(mentions: _FakeMentions, taxonomy: _FakeTaxonomyWrites | None = None):
    return ConceptPromotionService(
        taxonomy or _FakeTaxonomyWrites(),  # type: ignore[arg-type]
        mentions,  # type: ignore[arg-type]
    )


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

    terms = await list_unmapped_terms(50, 1, USER, mentions)  # type: ignore[arg-type]

    assert [term.normalized_text for term in terms] == ["kubernetes", "git", "kuberentes"]
    assert terms[0].occurrences == 412


@pytest.mark.asyncio
async def test_the_queue_page_size_is_capped() -> None:
    """The queue grows with the corpus. An unbounded `limit` in a query string
    turns one request into a full table read."""
    mentions = _FakeMentions([(f"term{i}", f"Term{i}", i) for i in range(600)])

    terms = await list_unmapped_terms(10_000, 1, USER, mentions)  # type: ignore[arg-type]

    assert len(terms) == 500


# --- recording a decision ----------------------------------------------------


@pytest.mark.asyncio
async def test_reviewing_a_term_records_the_decision_that_was_made() -> None:
    mentions = _FakeMentions([("kubernetes", "Kubernetes", 412)])

    taxonomy = _FakeTaxonomyWrites()

    response = await review_unmapped_term(
        "kubernetes",
        ReviewUnmappedRequest(status="promoted"),
        USER,
        _promotion(mentions, taxonomy),
    )

    assert [(term, status) for term, status, _ in mentions.reviewed] == [("kubernetes", "promoted")]
    assert (response.normalized_text, response.status) == ("kubernetes", "promoted")
    assert response.created_concept is True


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
            _promotion(mentions),
        )

    assert raised.value.status_code == 404
    assert mentions.reviewed == []


@pytest.mark.asyncio
async def test_a_decision_can_be_taken_back() -> None:
    """Reviewing is a long column of near-identical rows and a fast hand. A
    screen where the wrong click cannot be undone is one people are right to be
    slow and nervous on."""
    mentions = _FakeMentions([("kubernetes", "Kubernetes", 412)])

    await review_unmapped_term(
        "kubernetes",
        ReviewUnmappedRequest(status="pending"),
        USER,
        _promotion(mentions),
    )

    assert [(term, status) for term, status, _ in mentions.reviewed] == [("kubernetes", "pending")]


def test_a_decision_must_be_one_of_the_three_the_workflow_defines() -> None:
    """`deleted`, `merged`, anything else — a status the review flow does not
    understand would leave the term neither queued nor acted on."""
    with pytest.raises(ValueError):
        ReviewUnmappedRequest(status="deleted")  # type: ignore[arg-type]


# --- promotion actually changes the taxonomy (spec 9.4) ----------------------


@pytest.mark.asyncio
async def test_promoting_a_term_creates_a_concept_the_linker_can_find() -> None:
    """The half that was missing. Before this, `promoted` and `ignored` differed
    only in the word stored, and the term came back unmapped on the next
    vacancy — ESCO has no Terraform and never will."""
    mentions = _FakeMentions([("terraform", "Terraform", 109)])
    taxonomy = _FakeTaxonomyWrites()

    outcome = await _promotion(mentions, taxonomy).review("terraform", "promoted")

    assert outcome is not None and outcome.created is True
    assert taxonomy.created == [("terraform", "Terraform", ["terraform", "Terraform"])]


@pytest.mark.asyncio
async def test_the_concept_is_labelled_the_way_a_person_wrote_it() -> None:
    """The index matches on the normalized form either way, so the visible label
    should be the one the documents used: "CI/CD", not "ci cd"."""
    mentions = _FakeMentions([("ci cd", "CI/CD", 126)])
    taxonomy = _FakeTaxonomyWrites()

    await _promotion(mentions, taxonomy).review("ci cd", "promoted")

    assert taxonomy.created[0][1] == "CI/CD"


@pytest.mark.asyncio
async def test_promoting_the_same_term_twice_does_not_make_a_rival_concept() -> None:
    """Two people, a double click, a re-run of a bulk review. Two concepts with
    one label is exactly the `ambiguous` state 9.3 reserves for real ambiguity,
    manufactured out of nothing."""
    mentions = _FakeMentions([("terraform", "Terraform", 109)])
    taxonomy = _FakeTaxonomyWrites()
    service = _promotion(mentions, taxonomy)

    first = await service.review("terraform", "promoted")
    second = await service.review("terraform", "promoted")

    assert first is not None and second is not None
    assert first.concept_id == second.concept_id
    assert (first.created, second.created) == (True, False)
    assert len(taxonomy.created) == 1


@pytest.mark.asyncio
async def test_ignoring_a_term_creates_nothing() -> None:
    mentions = _FakeMentions([("environment", "environment", 557)])
    taxonomy = _FakeTaxonomyWrites()

    outcome = await _promotion(mentions, taxonomy).review("environment", "ignored")

    assert outcome is not None and outcome.concept_id is None
    assert taxonomy.created == []


@pytest.mark.asyncio
async def test_undo_detaches_the_term_from_its_concept() -> None:
    """A term back in the queue that still points at a concept would claim to be
    both promoted and awaiting a decision."""
    mentions = _FakeMentions([("terraform", "Terraform", 109)])
    service = _promotion(mentions, _FakeTaxonomyWrites())

    await service.review("terraform", "promoted")
    await service.review("terraform", "pending")

    assert mentions.reviewed[-1] == ("terraform", "pending", None)


# --- reviewing a page at a time ----------------------------------------------


@pytest.mark.asyncio
async def test_a_page_of_decisions_is_applied_in_one_request() -> None:
    """The queue's top hundred are 16% of occurrences and the top thousand 47%,
    so there is no short prefix a person can review and be done."""
    mentions = _FakeMentions(
        [("terraform", "Terraform", 109), ("grafana", "Grafana", 83), ("skills", "skills", 725)]
    )
    taxonomy = _FakeTaxonomyWrites()

    response = await review_unmapped_terms(
        BulkReviewRequest(
            decisions={"terraform": "promoted", "grafana": "promoted", "skills": "ignored"}
        ),
        USER,
        _promotion(mentions, taxonomy),
    )

    assert (response.reviewed, response.promoted, response.concepts_created) == (3, 2, 2)
    assert response.missing == []


@pytest.mark.asyncio
async def test_a_stale_term_is_named_rather_than_failing_the_batch() -> None:
    """A page open in a browser while the queue moved on should not cost the
    other decisions on it."""
    mentions = _FakeMentions([("terraform", "Terraform", 109)])

    response = await review_unmapped_terms(
        BulkReviewRequest(decisions={"terraform": "promoted", "gone": "ignored"}),
        USER,
        _promotion(mentions),
    )

    assert response.reviewed == 1
    assert response.missing == ["gone"]


# --- the queue's frequency floor ---------------------------------------------


@pytest.mark.asyncio
async def test_terms_seen_once_are_hidden_by_default_not_deleted() -> None:
    """8 162 of 11 504 pending terms were seen exactly once: `texture
    resolution`, `domain expertise`. They stay as evidence, and a term that
    recurs leaves the tail by recurring."""
    mentions = _FakeMentions(
        [("terraform", "Terraform", 109), ("texture resolution", "texture resolution", 1)]
    )

    filtered = await list_unmapped_terms(50, 2, USER, mentions)  # type: ignore[arg-type]
    everything = await list_unmapped_terms(50, 1, USER, mentions)  # type: ignore[arg-type]

    assert [t.normalized_text for t in filtered] == ["terraform"]
    assert len(everything) == 2


@pytest.mark.asyncio
async def test_undo_retires_the_concept_the_promotion_created() -> None:
    """Clearing the column alone would leave the concept in the alias index, so
    the term would keep linking to something the reviewer just said should not
    exist — the one state a review screen must not be able to produce."""
    mentions = _FakeMentions([("sports", "sports", 168)])
    taxonomy = _FakeTaxonomyWrites()
    service = _promotion(mentions, taxonomy)

    promoted = await service.review("sports", "promoted")
    await service.review("sports", "pending")

    assert promoted is not None
    assert taxonomy.retired == [uuid.UUID(promoted.concept_id or "")]


@pytest.mark.asyncio
async def test_ignoring_a_promoted_term_also_retires_its_concept() -> None:
    """Undo and "actually, this is not a skill" are the same correction; only
    the label recorded differs."""
    mentions = _FakeMentions([("clean", "clean", 231)])
    taxonomy = _FakeTaxonomyWrites()
    service = _promotion(mentions, taxonomy)

    await service.review("clean", "promoted")
    await service.review("clean", "ignored")

    assert len(taxonomy.retired) == 1
