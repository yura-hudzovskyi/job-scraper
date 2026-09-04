"""HTML to ordered blocks, via the domain's BlockBuilder.

Lives in integrations rather than in the domain because BeautifulSoup is an
adapter concern — the same reason the DOU and Djinni parsers live here. The part
worth protecting, the offset arithmetic, stays in
app/domain/documents/parsing.py and this module only decides which tag becomes
which block.

That mapping is structural throughout: `<h2>` is a heading because the author
marked it as one, not because of what it says. Nothing here reads the text.

The walk distinguishes inline from block-level markup, which is the difference
between one readable paragraph and a block per `<span>`. Real vacancy HTML is
mixed content — a `<div>` holding a sentence, a `<br>`, then a `<ul>` — so bare
text sitting between block elements is collected rather than dropped.
"""

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from app.domain.documents.models import BlockType
from app.domain.documents.parsing import BlockBuilder, ParsedDocument

# Tag -> block type. A block-level tag that is not listed still becomes a block;
# it just gets PARAGRAPH, because "this is a chunk of prose" is all a <div> or a
# <section> actually tells us.
_BLOCK_TAGS: dict[str, BlockType] = {
    "h1": BlockType.HEADING,
    "h2": BlockType.HEADING,
    "h3": BlockType.HEADING,
    "h4": BlockType.HEADING,
    "h5": BlockType.HEADING,
    "h6": BlockType.HEADING,
    "li": BlockType.LIST_ITEM,
    "p": BlockType.PARAGRAPH,
    "blockquote": BlockType.PARAGRAPH,
    "pre": BlockType.PARAGRAPH,
    "td": BlockType.TABLE_CELL,
    "th": BlockType.TABLE_CELL,
    "dt": BlockType.HEADING,
    "dd": BlockType.PARAGRAPH,
}

# Part of the surrounding text, never a block of their own. Splitting on these
# would turn "we use <b>Python</b> daily" into three fragments, and an evidence
# span could then only ever quote one of them.
_INLINE_TAGS = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "dfn", "em",
        "i", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span",
        "strike", "strong", "sub", "sup", "time", "tt", "u", "var", "wbr", "font",
    }
)

# Never contribute text: script/style carry code, and nav/footer chrome belongs
# to the site rather than to the vacancy.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "head", "nav", "footer", "template"})


def _is_block_level(tag: Tag) -> bool:
    return tag.name not in _INLINE_TAGS and tag.name not in _SKIP_TAGS


def _has_block_descendant(tag: Tag) -> bool:
    return any(
        isinstance(descendant, Tag) and _is_block_level(descendant)
        for descendant in tag.descendants
    )


def _emit(node: Tag, builder: BlockBuilder) -> None:
    """Walk in document order, emitting the innermost block-level element.

    Inline markup and bare text accumulate into `pending` and are flushed as one
    paragraph when the next block-level element arrives. That is what keeps
    `<div>intro text<p>para</p></div>` from losing "intro text", which walking
    only tags would do silently.

    A block containing another block yields the children only — emitting both
    would store the same sentence twice at two different offsets, and a span
    pointing at either would look equally valid.
    """
    pending: list[str] = []

    def flush() -> None:
        if pending:
            builder.add(" ".join(pending), BlockType.PARAGRAPH)
            pending.clear()

    for child in node.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                pending.append(text)
            continue
        if not isinstance(child, Tag) or child.name in _SKIP_TAGS:
            continue
        if not _is_block_level(child):
            text = child.get_text(" ", strip=True)
            if text:
                pending.append(text)
            continue

        flush()
        if _has_block_descendant(child):
            _emit(child, builder)
        else:
            builder.add(
                child.get_text(" ", strip=True),
                _BLOCK_TAGS.get(child.name, BlockType.PARAGRAPH),
            )

    flush()


def parse_html(raw: str) -> ParsedDocument:
    """Ordered blocks from an HTML fragment or a whole page.

    `lxml` is the parser everywhere else in this repository, so it is the one
    used here: two parsers disagree on malformed markup, and that disagreement
    would show up as offsets shifting between re-parses of the same document.
    """
    soup = BeautifulSoup(raw, "lxml")
    builder = BlockBuilder()

    title = soup.find("title")
    if isinstance(title, Tag):
        builder.add(title.get_text(" ", strip=True), BlockType.TITLE)

    body = soup.find("body")
    _emit(body if isinstance(body, Tag) else soup, builder)

    return builder.build()
