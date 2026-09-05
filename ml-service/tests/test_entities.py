"""What the service refuses to pass on, and why each refusal matters.

Every test here is about an offset. Spec 3.5.2 condition 1 makes the offset the
thing that turns a model's guess into evidence, and an offset that is merely
*present* is worth nothing — it has to quote the document. These run without
torch on purpose: the rules are the part that has to be right, and needing 1.2 GB
of weights to check them would mean nobody checks them.
"""

from app.entities import Entity, best_label_per_span, collect, truncate

TEXT = (
    "Senior Python Developer\n\n"
    "We need strong Python and PostgreSQL skills. Experience with Docker and "
    "Kubernetes is required. Python 3.11 preferred."
)


def _raw(*items: dict[str, object]) -> dict[str, object]:
    groups: dict[str, list[dict[str, object]]] = {}
    for item in items:
        groups.setdefault(str(item.pop("label", "technology")), []).append(item)
    return {"entities": groups}


# --- what gets through -------------------------------------------------------


def test_a_span_that_quotes_the_document_is_kept() -> None:
    start = TEXT.index("PostgreSQL")
    raw = _raw({"text": "PostgreSQL", "start": start, "end": start + 10, "confidence": 0.98})

    entities, rejected = collect(raw, TEXT)

    assert rejected == 0
    assert entities == [
        Entity("technology", "PostgreSQL", start, start + 10, 0.98),
    ]


def test_the_same_word_in_three_places_stays_three_mentions() -> None:
    """The reason offsets are demanded at all. "Python" occurs three times here,
    and a result carrying only the string could not say which one it meant."""
    starts = [7, 40, 121]
    assert [TEXT[s : s + 6] for s in starts] == ["Python"] * 3
    raw = _raw(*({"text": "Python", "start": s, "end": s + 6, "confidence": 0.9} for s in starts))

    entities, rejected = collect(raw, TEXT)

    assert rejected == 0
    assert [e.start_char for e in entities] == starts


# --- what does not -----------------------------------------------------------


def test_a_span_pointing_at_the_wrong_words_is_rejected() -> None:
    """The failure this whole module exists to catch: an offset that is in range
    and wrong. Nothing downstream could detect it — the span would resolve, and
    quote text the model never saw."""
    raw = _raw({"text": "PostgreSQL", "start": 0, "end": 10, "confidence": 0.99})

    entities, rejected = collect(raw, TEXT)

    assert entities == []
    assert rejected == 1


def test_a_bare_string_is_rejected_rather_than_located_by_search() -> None:
    """What the model returns without include_spans. Searching for it would pick
    the first "Python" in a document with three, and be right by luck."""
    raw = {"entities": {"technology": ["Python", "PostgreSQL"]}}

    entities, rejected = collect(raw, TEXT)

    assert entities == []
    assert rejected == 2


def test_an_out_of_range_span_is_rejected_not_clamped() -> None:
    """Python slicing is forgiving — TEXT[500:600] is "" rather than an error —
    so this would otherwise pass as an entity with empty evidence."""
    raw = _raw({"text": "Kubernetes", "start": 500, "end": 510, "confidence": 0.9})

    entities, rejected = collect(raw, TEXT)

    assert entities == []
    assert rejected == 1


def test_output_with_no_entities_key_is_empty_rather_than_an_error() -> None:
    entities, rejected = collect({}, TEXT)

    assert (entities, rejected) == ([], 0)


# --- one mention per span ----------------------------------------------------


def test_competing_labels_on_one_span_collapse_to_the_confident_one() -> None:
    """Measured behaviour, not a hypothetical: the model returns "Python" as
    `technology` at 0.97 and as `tool` at 0.66. They are one mention of one word,
    and keeping both double-counts it everywhere downstream."""
    entities = [
        Entity("tool", "Python", 7, 13, 0.66),
        Entity("technology", "Python", 7, 13, 0.97),
    ]

    assert best_label_per_span(entities) == [Entity("technology", "Python", 7, 13, 0.97)]


def test_overlapping_but_different_spans_are_both_kept() -> None:
    """ "Apache Kafka" and "Kafka" are two spans, and choosing between them needs
    the taxonomy. That is the linker's decision, not this service's."""
    entities = [
        Entity("technology", "Apache Kafka", 0, 12, 0.9),
        Entity("technology", "Kafka", 7, 12, 0.8),
    ]

    assert len(best_label_per_span(entities)) == 2


def test_entities_come_back_in_document_order() -> None:
    """Same input, same output (spec 2.6). Left alone, the order would follow
    whichever label the model happened to score first."""
    entities = [
        Entity("technology", "Kubernetes", 97, 107, 0.9),
        Entity("technology", "Python", 7, 13, 0.9),
        Entity("technology", "Docker", 86, 92, 0.9),
    ]

    assert [e.start_char for e in best_label_per_span(entities)] == [7, 86, 97]


# --- truncation --------------------------------------------------------------


def test_a_short_document_is_not_touched() -> None:
    assert truncate(TEXT, 12_000) == (TEXT, False)


def test_a_long_document_is_cut_and_says_so() -> None:
    """The flag matters more than the cut: the offsets index into what the model
    was given, and a caller that thinks it sent the whole document will trust
    the absence of a term in the tail it never sent."""
    cut, truncated = truncate("x" * 100, 40)

    assert truncated is True
    assert len(cut) == 40


def test_truncation_cuts_the_tail_so_earlier_offsets_stay_valid() -> None:
    """Every offset the model returns is an offset into the truncated string.
    They match the original only because what was removed came after them."""
    original = TEXT + " " + "padding " * 200
    cut, _ = truncate(original, len(TEXT))

    assert original.startswith(cut)
