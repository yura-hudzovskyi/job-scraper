"""Splitting a rerank into requests the provider will accept.

The failure this fixes ran in production for weeks without a single successful
rerank: 500 vacancies in one call, a bare 400 back, the exception swallowed by
the batch handler, and every match scored on embedding similarity alone while
the config said otherwise.

The budget is in UTF-8 bytes rather than characters, and that is the substance
rather than a detail. 500 real vacancies came to 1 548 088 characters — about
387 000 tokens by the usual chars/4 estimate — and Voyage counted 931 442
against a 600 000 limit. Cyrillic costs two to three times what the rule of
thumb assumes.
"""

from app.integrations.voyage import MAX_RERANK_BATCH_BYTES, _batches, _byte_size


def _docs(count: int, size: int) -> list[str]:
    return ["x" * size for _ in range(count)]


def test_a_small_set_goes_in_one_request() -> None:
    assert _batches(_docs(10, 1000), "query") == [list(range(10))]


def test_every_document_appears_exactly_once() -> None:
    """A dropped document is a vacancy silently missing from the ranking, which
    is invisible in the result and looks like a retrieval miss."""
    batches = _batches(_docs(500, 4000), "query")

    seen = [index for batch in batches for index in batch]
    assert sorted(seen) == list(range(500))
    assert len(seen) == len(set(seen))


def test_no_batch_exceeds_the_budget() -> None:
    documents = _docs(500, 4000)
    query = "q" * 5000

    for batch in _batches(documents, query):
        total = _byte_size(query) + sum(_byte_size(documents[index]) for index in batch)
        assert total <= MAX_RERANK_BATCH_BYTES


def test_cyrillic_counts_by_bytes_not_characters() -> None:
    """The bug in one test. These documents are half the character count of the
    Latin ones and the same byte count, because Cyrillic is two bytes each — and
    it is bytes the tokenizer charges for."""
    cyrillic = _batches(["і" * 2000] * 200, "q")
    latin = _batches(["i" * 2000] * 200, "q")

    assert len(cyrillic) > len(latin)


def test_the_query_is_counted_against_every_batch() -> None:
    """It is sent with every request, so a budget that ignores it is wrong by
    the size of the CV on each call."""
    documents = _docs(40, 10_000)

    with_small_query = _batches(documents, "q")
    with_large_query = _batches(documents, "q" * 400_000)

    assert len(with_large_query) > len(with_small_query)


def test_a_single_oversized_document_still_gets_sent() -> None:
    """Refusing it here would drop a vacancy from the ranking silently. The
    provider truncating it is the better failure: it shows up in the score, and
    documents are capped upstream anyway."""
    batches = _batches(["x" * (MAX_RERANK_BATCH_BYTES * 2)], "q")

    assert batches == [[0]]


def test_a_query_larger_than_the_budget_falls_back_to_one_per_request() -> None:
    documents = _docs(3, 100)

    assert _batches(documents, "q" * (MAX_RERANK_BATCH_BYTES + 1)) == [[0], [1], [2]]


def test_no_documents_means_no_requests() -> None:
    assert _batches([], "query") == []
