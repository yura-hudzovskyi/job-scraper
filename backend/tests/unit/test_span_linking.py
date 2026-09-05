"""Linking what the model found, rather than every word in the document.

Spec 9.3 opens with "for every extracted mention". Scanning the whole text was
the honest approximation of that while there was no model to extract mentions,
and it has a measurable cost: it asks the taxonomy about every word in a vacancy,
so ESCO labels containing "skills", "environment" and "communication" turn
ordinary prose into candidate terms. Those three are the top of the real review
queue in production, and none of them is a skill anybody asked for.
"""

import uuid

from app.domain.taxonomy.linking import (
    AliasIndex,
    ExtractedSpan,
    LinkStatus,
    deduplicate,
    find_mentions,
    link_spans,
)

PYTHON = uuid.uuid4()
SHEETS = uuid.uuid4()
GOOGLE = uuid.uuid4()
NURSING = uuid.uuid4()
COMMUNICATION = uuid.uuid4()


def _index() -> AliasIndex:
    return AliasIndex.build(
        [
            (PYTHON, ["Python", "Python (computer programming)"]),
            (SHEETS, ["Google Sheets"]),
            (GOOGLE, ["Google"]),
            (NURSING, ["nursing", "nursing care", "provide nursing care"]),
            # ESCO really does carry this as a label, which is how an ordinary
            # English word ends up indexed as a taxonomy term.
            (COMMUNICATION, ["communication"]),
        ]
    )


def _span(text: str, document: str, confidence: float = 0.9) -> ExtractedSpan:
    start = document.index(text)
    return ExtractedSpan(text, start, start + len(text), confidence)


# --- the whole phrase wins ---------------------------------------------------


def test_a_span_that_is_a_taxonomy_label_links_to_it() -> None:
    document = "We use Python here."

    linked = link_spans([_span("Python", document)], _index())

    assert [m.concept_ids for m in linked] == [[PYTHON]]
    assert linked[0].link_status == LinkStatus.LINKED


def test_the_longer_phrase_beats_the_word_inside_it() -> None:
    """ "Google Sheets" is a better answer than "Google", and it is only
    available because the model handed over the whole phrase."""
    document = "Experience with Google Sheets required."

    linked = link_spans([_span("Google Sheets", document)], _index())

    assert [m.concept_ids for m in linked] == [[SHEETS]]


def test_offsets_still_point_into_the_document(_document: str = "Daily Python work") -> None:
    linked = link_spans([_span("Python", _document)], _index())

    span = linked[0]
    assert _document[span.start_char : span.end_char] == "Python"


# --- falling back inside the span --------------------------------------------


def test_a_phrase_the_taxonomy_lacks_is_matched_by_what_it_contains() -> None:
    """ "Google Cloud Storage" is not an ESCO label; "Google" is. Answering with
    the part that is known beats answering with nothing."""
    document = "We run on Google Cloud Storage daily."

    linked = link_spans([_span("Google Cloud Storage", document)], _index())

    assert [m.concept_ids for m in linked] == [[GOOGLE]]


def test_a_fallback_match_is_located_in_the_document_not_in_the_span() -> None:
    """The inner search runs against the span's own text, so its offsets start
    at zero. Left unshifted, every fallback mention would point at the first
    twenty characters of the vacancy."""
    document = "Requirements: strong Google Cloud Storage experience."
    span = _span("Google Cloud Storage", document)

    linked = link_spans([span], _index())

    found = linked[0]
    assert document[found.start_char : found.end_char] == "Google"
    assert found.start_char == document.index("Google")


# --- refusing ----------------------------------------------------------------


def test_a_span_matching_nothing_is_unmapped_not_forced() -> None:
    """Spec 9.3: forced linking is forbidden, and a correct NIL beats a wrong
    taxonomy id."""
    document = "We need Kubernetes experience."

    linked = link_spans([_span("Kubernetes", document)], _index())

    assert linked[0].concept_ids == []
    assert linked[0].link_status == LinkStatus.UNMAPPED
    assert linked[0].raw_text == "Kubernetes"


def test_an_unmapped_span_keeps_the_model_s_words_for_review() -> None:
    """It becomes a row in the unmapped queue (9.4), and the queue is only
    useful if it says what was actually written."""
    document = "Досвід роботи з M.E.Doc обов'язковий."

    linked = link_spans([_span("M.E.Doc", document)], _index())

    assert linked[0].raw_text == "M.E.Doc"


# --- why this replaced the scan ----------------------------------------------


def test_scanning_the_text_finds_words_the_model_never_called_skills() -> None:
    """The behaviour being moved away from, pinned so the difference is visible.

    "communication" is a real ESCO label, so a sentence merely using the word
    becomes a candidate term under a full-text scan. In production that put
    `communication` in the review queue 453 times, next to `skills` at 725 and
    `environment` at 557 — none of them a skill any vacancy asked for."""
    document = "We value open communication, and we use Python daily."
    index = _index()

    scanned = deduplicate(find_mentions(document, index))
    from_model = link_spans([_span("Python", document)], index)

    assert sorted(m.raw_text for m in scanned) == ["Python", "communication"]
    assert [m.raw_text for m in from_model] == ["Python"]


def test_the_model_s_confidence_is_carried_through_not_replaced() -> None:
    """The row stores `extraction_confidence`, and before this it stored 1.0 for
    everything — which made a 0.6 guess indistinguishable from a certainty."""
    document = "Some Python here."

    spans = [_span("Python", document, confidence=0.61)]
    linked = link_spans(spans, _index())

    assert spans[0].confidence == 0.61
    assert linked[0].normalized_text == "python"


def test_an_empty_span_list_links_nothing_rather_than_scanning() -> None:
    assert link_spans([], _index()) == []
