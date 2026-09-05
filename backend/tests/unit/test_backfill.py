"""Parsing a stored revision when nothing recorded which form its text is in.

`document_revisions.raw_text` holds two different things: the flattened
description the Phase 1 migration copied out of `job_source_records`, and the
source markup everything since ingests. Nothing on the row says which, so the
backfill sniffs — and getting the sniff wrong is not a cosmetic failure. Markup
parsed as plain text puts tags inside the evidence spans, and a span is supposed
to quote the document exactly.
"""

from app.workers.tasks.backfill import parse_stored_text

PLAIN = """Ми шукаємо Python розробника

Вимоги:
- 3 роки досвіду
- знання Django

Ми пропонуємо конкурентну зарплату."""

MARKUP = """<div class="description">
<p>We are looking for a Python developer.</p>
<ul><li>3 years of experience</li><li>Django</li></ul>
</div>"""


def test_markup_is_parsed_as_markup() -> None:
    parsed = parse_stored_text(MARKUP)

    assert "<p>" not in parsed.text
    assert "<li>" not in parsed.text
    assert "Django" in parsed.text


def test_flattened_text_is_parsed_as_text() -> None:
    parsed = parse_stored_text(PLAIN)

    assert "Ми шукаємо Python розробника" in parsed.text
    assert len(parsed.blocks) > 1


def test_every_block_quotes_the_text_it_indexes_into() -> None:
    """The invariant the whole evidence chain rests on, checked for both forms:
    parsed_text[start:end] is the block's own text, whichever parser ran."""
    for raw in (PLAIN, MARKUP):
        parsed = parse_stored_text(raw)
        for block in parsed.blocks:
            assert parsed.text[block.start_char : block.end_char] == block.text


def test_prose_that_merely_mentions_a_tag_is_not_markup() -> None:
    """A vacancy asking for HTML skills is still plain text. Handing it to the
    HTML parser would drop the sentence that contains the angle brackets."""
    raw = "Знання HTML обов'язкове. Треба розуміти, як працює <head> сторінки."

    parsed = parse_stored_text(raw)

    assert "<head>" in parsed.text


def test_an_empty_document_parses_to_nothing_rather_than_failing() -> None:
    """A revision whose source page was empty must not sink the batch it is in."""
    parsed = parse_stored_text("   \n\n  ")

    assert parsed.blocks == []
