"""Block parsing, and the offset invariant everything downstream depends on.

Most of these assert the same property from different angles:
`text[start:end] == block.text`. That looks repetitive written out, and it is
the point — the failure it guards against is a Phase 3 evidence span quoting
text the extractor never read, which no other test in the suite would catch.
"""

from app.domain.documents.models import BlockType
from app.domain.documents.parsing import (
    MAX_DOCUMENT_CHARS,
    PARSER_VERSION,
    BlockBuilder,
    looks_like_list_item,
    normalize_block_text,
    parse_plain_text,
)
from app.integrations.parsers.html import parse_html

# --- the invariant -----------------------------------------------------------


def test_plain_text_spans_resolve_to_their_own_text() -> None:
    document = parse_plain_text("First paragraph.\n\nSecond paragraph.")

    assert document.spans_resolve()
    for block in document.blocks:
        assert document.text[block.start_char : block.end_char] == block.text


def test_html_spans_resolve_to_their_own_text() -> None:
    document = parse_html(
        "<h2>Requirements</h2><ul><li>Python</li><li>Postgres</li></ul><p>Nice place.</p>"
    )

    assert document.spans_resolve()
    for block in document.blocks:
        assert document.text[block.start_char : block.end_char] == block.text


def test_offsets_survive_non_ascii_text() -> None:
    """Offsets are character indices, not byte indices. Cyrillic is two bytes per
    character in UTF-8, so a byte-based implementation passes the ASCII tests
    above and silently misquotes every Ukrainian vacancy."""
    document = parse_plain_text("Вимоги до кандидата\n\nПотрібен Python та PostgreSQL")

    assert document.spans_resolve()
    assert document.blocks[1].text.startswith("Потрібен")
    assert document.text[document.blocks[1].start_char :].startswith("Потрібен")


def test_offsets_survive_emoji_outside_the_basic_plane() -> None:
    document = parse_plain_text("Remote 🌍 team\n\nWe ship 🚀 often")

    assert document.spans_resolve()


def test_blocks_are_contiguous_and_ordered() -> None:
    document = parse_html("<p>One</p><p>Two</p><p>Three</p>")

    ordinals = [block.ordinal for block in document.blocks]
    starts = [block.start_char for block in document.blocks]
    assert ordinals == [0, 1, 2]
    assert starts == sorted(starts)
    for block in document.blocks:
        assert block.end_char > block.start_char


def test_block_at_finds_the_block_containing_an_offset() -> None:
    document = parse_html("<h2>Requirements</h2><p>Python and Postgres</p>")
    target = document.blocks[1]

    found = document.block_at(target.start_char + 2)

    assert found is not None
    assert found.ordinal == target.ordinal
    assert document.block_at(len(document.text)) is None


# --- determinism -------------------------------------------------------------


def test_parsing_the_same_input_twice_gives_identical_output() -> None:
    raw = "<h2>Про нас</h2><ul><li>Python</li></ul>"

    first, second = parse_html(raw), parse_html(raw)

    assert first.text == second.text
    assert first.blocks == second.blocks


def test_line_endings_do_not_change_the_parsed_text() -> None:
    """The same document fetched twice, once through a source that normalises to
    CRLF, must not produce a second revision — content_hash is computed from this
    text."""
    unix = parse_plain_text("First line\n\nSecond line")
    windows = parse_plain_text("First line\r\n\r\nSecond line")
    old_mac = parse_plain_text("First line\r\rSecond line")

    assert unix.text == windows.text == old_mac.text


def test_the_parser_version_is_recorded_on_every_document() -> None:
    """Stored on the revision, so offsets written by an older parser are
    identifiable instead of being mixed with newer ones."""
    assert parse_plain_text("text").parser_version == PARSER_VERSION
    assert parse_html("<p>text</p>").parser_version == PARSER_VERSION


# --- normalization -----------------------------------------------------------


def test_horizontal_whitespace_collapses_but_text_is_preserved() -> None:
    assert normalize_block_text("  Python    and\tPostgres  ") == "Python and Postgres"


def test_non_breaking_and_zero_width_characters_fold_to_plain_text() -> None:
    """A vacancy pasted from a word processor is full of these. Left alone, the
    same visible skill hashes differently depending on where it was typed."""
    assert normalize_block_text("Python and\u200bPostgres") == "Python andPostgres"


def test_empty_and_whitespace_only_blocks_are_dropped() -> None:
    document = parse_html("<p></p><p>   </p><p>Real content</p>")

    assert len(document.blocks) == 1
    assert document.blocks[0].text == "Real content"


# --- structure, not meaning --------------------------------------------------


def test_html_headings_lists_and_cells_keep_their_kind() -> None:
    document = parse_html(
        "<h3>Requirements</h3><ul><li>Python</li></ul><table><tr><td>Salary</td></tr></table>"
    )

    kinds = {block.text: block.block_type for block in document.blocks}
    assert kinds["Requirements"] is BlockType.HEADING
    assert kinds["Python"] is BlockType.LIST_ITEM
    assert kinds["Salary"] is BlockType.TABLE_CELL


def test_plain_text_bullets_become_list_items() -> None:
    document = parse_plain_text("Requirements:\n- Python\n- Postgres\n1. Docker")

    kinds = [block.block_type for block in document.blocks]
    assert kinds == [
        BlockType.PARAGRAPH,
        BlockType.LIST_ITEM,
        BlockType.LIST_ITEM,
        BlockType.LIST_ITEM,
    ]


def test_plain_text_does_not_invent_headings() -> None:
    """`Requirements:` on its own line is a heading by convention only. Guessing
    from convention is inference about meaning, and a wrong section label would
    turn a "nice to have" into a hard requirement in Phase 3."""
    document = parse_plain_text("Requirements:\n\nPython")

    assert all(block.block_type is not BlockType.HEADING for block in document.blocks)


def test_a_bullet_marker_is_recognised_but_a_hyphenated_word_is_not() -> None:
    assert looks_like_list_item("- Python")
    assert looks_like_list_item("• Python")
    assert looks_like_list_item("2. Python")
    assert not looks_like_list_item("well-known frameworks")


def test_nested_block_elements_are_not_stored_twice() -> None:
    """A <p> inside an <li> must yield one block. Emitting both would store the
    same sentence at two offsets, and a span pointing at either would look
    equally valid."""
    document = parse_html("<ul><li><p>Python</p></li></ul>")

    assert [block.text for block in document.blocks] == ["Python"]


def test_script_and_style_contribute_nothing() -> None:
    document = parse_html("<p>Real</p><script>var x = 1;</script><style>.a{color:red}</style>")

    assert [block.text for block in document.blocks] == ["Real"]
    assert "var x" not in document.text


def test_markup_with_no_block_elements_still_yields_its_text() -> None:
    """A fragment of bare <span>s would otherwise store an empty parsed_text for a
    document that plainly has text in it."""
    document = parse_html("<span>Python</span> and <span>Postgres</span>")

    assert document.blocks
    assert "Python" in document.text
    assert document.spans_resolve()


def test_inline_markup_does_not_split_a_sentence_into_fragments() -> None:
    """Splitting on <b> would leave an evidence span able to quote only "Python"
    or only "we use", never the sentence a human would want to read."""
    document = parse_html("<p>We use <b>Python</b> and <i>Postgres</i> daily.</p>")

    assert [block.text for block in document.blocks] == ["We use Python and Postgres daily."]


def test_bare_text_between_block_elements_is_not_dropped() -> None:
    """Real vacancy markup is mixed content. Walking only tags loses the
    sentence sitting directly inside the <div>, silently and with no warning."""
    document = parse_html("<div>Intro sentence.<p>A paragraph.</p></div>")

    texts = [block.text for block in document.blocks]
    assert "Intro sentence." in texts
    assert "A paragraph." in texts


def test_an_unmapped_block_level_tag_still_becomes_a_block() -> None:
    document = parse_html("<section>Standalone text in a section</section>")

    assert [block.text for block in document.blocks] == ["Standalone text in a section"]
    assert document.blocks[0].block_type is BlockType.PARAGRAPH


def test_script_text_is_not_resurrected_when_it_is_the_only_content() -> None:
    """A document of nothing but <script> must parse to nothing. An earlier
    fallback here re-extracted the whole page's text when no blocks were found,
    which put JavaScript source into parsed_text."""
    document = parse_html("<script>var secret = 1;</script>")

    assert document.blocks == []
    assert "secret" not in document.text


def test_an_html_title_is_captured_as_the_title_block() -> None:
    document = parse_html("<html><head><title>Backend Engineer</title></head><body><p>x</p></body></html>")

    assert document.blocks[0].block_type is BlockType.TITLE
    assert document.blocks[0].text == "Backend Engineer"


# --- truncation --------------------------------------------------------------


def test_an_oversized_document_is_truncated_visibly() -> None:
    """Never silently: a truncated document that does not say so looks like a
    vacancy whose requirements simply end halfway."""
    builder = BlockBuilder(max_chars=20)
    builder.add("within the limit", BlockType.PARAGRAPH)
    builder.add("this one pushes past the ceiling", BlockType.PARAGRAPH)

    document = builder.build()

    assert document.truncated is True
    assert len(document.blocks) == 1
    assert document.warnings
    assert document.spans_resolve()


def test_a_normal_document_is_not_marked_truncated() -> None:
    document = parse_plain_text("Short and ordinary.")

    assert document.truncated is False
    assert document.warnings == []


def test_the_default_ceiling_is_far_above_a_real_vacancy() -> None:
    assert MAX_DOCUMENT_CHARS > 50_000


# --- degenerate input --------------------------------------------------------


def test_empty_input_parses_to_an_empty_document() -> None:
    for document in (parse_plain_text(""), parse_html("")):
        assert document.text == ""
        assert document.blocks == []
        assert document.spans_resolve()


def test_whitespace_only_input_parses_to_an_empty_document() -> None:
    assert parse_plain_text("   \n\n  \t ").blocks == []


def test_broken_markup_does_not_raise() -> None:
    document = parse_html("<p>Unclosed <b>bold <li>stray item")

    assert document.spans_resolve()
    assert document.text
