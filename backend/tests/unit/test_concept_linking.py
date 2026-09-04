"""Finding taxonomy terms in a document.

The cases that matter are the ones where a naive implementation is confidently
wrong: substring matches inside longer words, short abbreviations that appear
everywhere, and labels several concepts share. Each is drawn from what the real
ESCO v1.2.1 release actually contains.
"""

import uuid

from app.domain.taxonomy.linking import (
    MIN_FORM_CHARS,
    MIN_SPECIFICITY,
    AliasIndex,
    LinkStatus,
    deduplicate,
    find_mentions,
    tokenize,
)

PYTHON = uuid.uuid4()
MACHINE_LEARNING = uuid.uuid4()
LEARNING = uuid.uuid4()
GEOLOGIST_A = uuid.uuid4()
GEOLOGIST_B = uuid.uuid4()


def _index() -> AliasIndex:
    return AliasIndex.build(
        [
            (PYTHON, ["Python", "Python programming", "python3"]),
            (MACHINE_LEARNING, ["machine learning", "ML"]),
            (LEARNING, ["learning"]),
            (GEOLOGIST_A, ["geologist"]),
            (GEOLOGIST_B, ["geologist"]),
        ]
    )


# --- what it finds -----------------------------------------------------------


def test_a_label_in_the_text_is_found_with_a_span_that_quotes_it() -> None:
    text = "We need Python and a good attitude."

    mentions = find_mentions(text, _index())

    assert len(mentions) == 1
    found = mentions[0]
    assert found.concept_id == PYTHON
    assert text[found.start_char : found.end_char] == found.raw_text == "Python"


def test_matching_is_case_insensitive_but_quotes_the_original() -> None:
    mentions = find_mentions("we use PYTHON daily", _index())

    assert mentions[0].raw_text == "PYTHON"
    assert mentions[0].concept_id == PYTHON


def test_a_multi_word_label_is_one_mention() -> None:
    mentions = find_mentions("experience with machine learning required", _index())

    assert [m.raw_text for m in mentions] == ["machine learning"]


def test_the_longer_label_wins_where_two_overlap() -> None:
    """"machine learning" and "learning" both match. The more specific concept is
    the one the author meant, and emitting both would double-count the sentence.
    """
    mentions = find_mentions("machine learning", _index())

    assert len(mentions) == 1
    assert mentions[0].concept_id == MACHINE_LEARNING


def test_the_shorter_label_still_matches_on_its_own() -> None:
    mentions = find_mentions("continuous learning matters", _index())

    assert [m.concept_id for m in mentions] == [LEARNING]


# --- what it refuses to find -------------------------------------------------


def test_a_label_inside_a_longer_word_is_not_a_match() -> None:
    """Substring scanning finds "ML" inside "HTML" and "r" inside "your". Word
    tokenisation cannot, which is the reason for it."""
    mentions = find_mentions("we write HTML and PYTHONIC code", _index())

    assert mentions == []


def test_forms_too_short_to_be_safe_are_never_indexed() -> None:
    """ESCO lists r, ml, ux, io, bi and ng as alternative labels. Every one of
    them matches noise in a real vacancy."""
    index = AliasIndex.build([(uuid.uuid4(), ["r", "ml", "io", "ng"])])

    assert len(index) == 0
    assert find_mentions("r and ml and io and ng", index) == []


def test_the_minimum_length_keeps_the_abbreviations_worth_having() -> None:
    index = AliasIndex.build([(PYTHON, ["SQL", "AWS", "Git"])])

    assert len(index) == 3
    assert MIN_FORM_CHARS == 3


def test_ml_still_reaches_its_concept_through_the_full_label() -> None:
    """Dropping the two-letter form costs nothing that the long form does not
    already cover."""
    mentions = find_mentions("we do machine learning here", _index())

    assert mentions[0].concept_id == MACHINE_LEARNING


# --- ambiguity ---------------------------------------------------------------


def test_a_label_several_concepts_share_is_ambiguous_rather_than_guessed() -> None:
    """1 955 ESCO forms are shared; "geologist" by six concepts. Picking one
    would be inventing a fact — spec 9.3 step 6 stores the ambiguity instead."""
    mentions = find_mentions("looking for a geologist", _index())

    assert mentions[0].link_status == LinkStatus.AMBIGUOUS
    assert set(mentions[0].concept_ids) == {GEOLOGIST_A, GEOLOGIST_B}
    assert mentions[0].concept_id is None


def test_a_single_match_is_linked() -> None:
    mentions = find_mentions("Python", _index())

    assert mentions[0].link_status == LinkStatus.LINKED
    assert mentions[0].concept_id == PYTHON


def test_one_concept_listing_a_form_twice_does_not_look_ambiguous() -> None:
    index = AliasIndex.build([(PYTHON, ["Python", "python", "PYTHON"])])

    mentions = find_mentions("Python", index)

    assert mentions[0].link_status == LinkStatus.LINKED


# --- specificity -------------------------------------------------------------


def _wordy_index() -> AliasIndex:
    """A taxonomy where "design" is a component of many labels and "postgresql"
    of one — the ratio the real ESCO release has (design: 1334, postgresql: 1)."""
    concepts: list[tuple[uuid.UUID, list[str]]] = [
        (uuid.uuid4(), [f"design {noun}" for noun in ("systems", "parts", "tools")]),
        (uuid.uuid4(), ["graphic design", "product design", "design"]),
        (uuid.uuid4(), ["PostgreSQL"]),
    ]
    for extra in range(200):
        concepts.append((uuid.uuid4(), [f"design method {extra}"]))
    return AliasIndex.build(concepts)


def test_a_word_the_taxonomy_reuses_everywhere_scores_low() -> None:
    """"design" appears in 1 334 ESCO labels. A vacancy saying it almost never
    means the concept whose alternative label it happens to be."""
    index = _wordy_index()

    assert index.specificity_of("design") < MIN_SPECIFICITY
    assert index.specificity_of("postgresql") > 0.9


def test_a_generic_single_word_is_recorded_unmapped_rather_than_linked() -> None:
    """Spec 9.3 step 5: below the threshold it is stored as unmapped, because a
    correct `unmapped` beats a wrong concept id."""
    mentions = find_mentions("we design things with PostgreSQL", _wordy_index())
    by_text = {m.normalized_text: m for m in mentions}

    assert by_text["design"].link_status == LinkStatus.UNMAPPED
    assert by_text["postgresql"].link_status == LinkStatus.LINKED


def test_a_phrase_is_specific_by_construction() -> None:
    """Multi-word forms need no frequency defence — "graphic design" is a term
    even though "design" is not."""
    index = _wordy_index()

    assert index.specificity_of("graphic design") == 1.0
    assert find_mentions("graphic design work", index)[0].link_status == LinkStatus.LINKED


def test_specificity_comes_from_the_taxonomy_not_a_word_list() -> None:
    """The same word is specific in a taxonomy that barely uses it. Nothing here
    knows what English words are common — which is the line 25.3 draws."""
    sparse = AliasIndex.build([(uuid.uuid4(), ["design"])])

    assert sparse.specificity_of("design") > MIN_SPECIFICITY


# --- spans and repetition ----------------------------------------------------


def test_every_span_quotes_the_document_it_points_into() -> None:
    text = "Python, machine learning, and a geologist walk into a bar. Python again."

    for mention in find_mentions(text, _index()):
        assert text[mention.start_char : mention.end_char] == mention.raw_text


def test_a_repeated_term_is_one_fact_about_the_vacancy() -> None:
    text = "Python here. Python there. Python everywhere."

    unique = deduplicate(find_mentions(text, _index()))

    assert len(unique) == 1
    assert unique[0].start_char == 0, "the first occurrence is kept"


def test_deduplication_keeps_distinct_terms() -> None:
    mentions = deduplicate(find_mentions("Python and machine learning", _index()))

    assert {m.normalized_text for m in mentions} == {"python", "machine learning"}


# --- non-ASCII ---------------------------------------------------------------


def test_matching_works_in_a_cyrillic_document() -> None:
    """The corpus is largely Ukrainian, so tokenisation and folding have to be
    unicode-aware rather than ASCII."""
    concept = uuid.uuid4()
    index = AliasIndex.build([(concept, ["управління проєктами"])])

    mentions = find_mentions("Потрібне управління проєктами та досвід", index)

    assert len(mentions) == 1
    assert mentions[0].raw_text == "управління проєктами"
    assert mentions[0].concept_id == concept


def test_cyrillic_spans_resolve() -> None:
    index = AliasIndex.build([(uuid.uuid4(), ["Python"])])
    text = "Ми шукаємо розробника Python з досвідом"

    mention = find_mentions(text, index)[0]

    assert text[mention.start_char : mention.end_char] == "Python"


# --- degenerate input --------------------------------------------------------


def test_an_empty_document_yields_nothing() -> None:
    assert find_mentions("", _index()) == []


def test_an_empty_index_yields_nothing() -> None:
    assert find_mentions("Python and machine learning", AliasIndex()) == []


def test_tokenize_records_offsets_that_resolve() -> None:
    text = "Python, and  SQL."

    for token in tokenize(text):
        assert text[token.start : token.end] == token.text
