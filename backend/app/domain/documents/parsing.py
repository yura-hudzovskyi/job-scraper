"""Turning a raw document into ordered blocks with offsets that actually resolve.

The one invariant everything downstream leans on:

    parsed.text[block.start_char:block.end_char] == block.text

for every block, always. An evidence span produced in Phase 3 points at a range
of `parsed_text`, and a human is later shown that substring as the reason a
vacancy matched. If the offsets are off by even one character — one stray
non-breaking space, one `\\r\\n` collapsed differently on a re-parse — the quote
shown to the user is not the text the extractor read, and nothing in the system
would notice.

So offsets are never *searched for*. `BlockBuilder` emits the canonical text and
the blocks together, in one pass, and the offsets fall out of the construction.
The alternative — parse into blocks, then `text.find(block)` — is where that
class of bug comes from.

Deterministic by construction: same input and same PARSER_VERSION give the same
output, byte for byte. Bump PARSER_VERSION whenever that output would change, or
a stored revision's offsets stop meaning what they meant when they were written.
"""

import re
from dataclasses import dataclass, field

from app.domain.documents.models import BlockType

PARSER_NAME = "structural"
# Bump on any change to how text is normalized or blocks are split. Stored on
# document_revisions so a revision parsed by an older version is identifiable
# rather than silently mixed with newer offsets.
PARSER_VERSION = "1.0"

# Blocks are joined by exactly one newline. Chosen over "\n\n" so that the
# canonical text stays close to the source's own line count, and over "" so that
# adjacent blocks cannot merge into one word at the seam.
BLOCK_SEPARATOR = "\n"

# A defensive ceiling, not a token budget: nothing here calls a model. It exists
# so that one pathological document (a scraped page with a megabyte of inlined
# base64, a CV with a runaway table) cannot dominate a batch. Exceeding it is
# reported, never silent — see ParsedDocument.truncated.
MAX_DOCUMENT_CHARS = 200_000

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•·–—]|\d+[.)])\s+")


def normalize_block_text(text: str) -> str:
    """Collapse horizontal whitespace and strip the ends of one block's text.

    Newlines are left alone rather than collapsed: a `<pre>` block legitimately
    contains them, and since blocks are joined by a newline anyway, folding them
    here would change nothing except make the stored text differ from the source
    for no gain. Zero-width and non-breaking characters *are* folded, so that the
    same visible text pasted from a word processor hashes identically to the same
    text typed by hand.
    """
    folded = text.replace("\u00a0", " ").replace("\u200b", "")
    return _HORIZONTAL_WHITESPACE.sub(" ", folded).strip()


def looks_like_list_item(line: str) -> bool:
    """Whether a plain-text line opens with a bullet or a number.

    Typography, not meaning: this says "the author formatted this as a list",
    which is the same kind of structural fact as an <li> tag. It says nothing
    about what the item contains.
    """
    return _BULLET_PREFIX.match(line) is not None


@dataclass(frozen=True)
class ParsedBlock:
    ordinal: int
    block_type: BlockType
    text: str
    start_char: int
    end_char: int


@dataclass
class ParsedDocument:
    """The canonical text of a revision plus the blocks that compose it.

    `text` is what gets stored as `document_revisions.parsed_text`, and it is the
    only string the offsets are valid against. Store one without the other and
    every span becomes unverifiable.
    """

    text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    parser_name: str = PARSER_NAME
    parser_version: str = PARSER_VERSION
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)

    def block_at(self, offset: int) -> ParsedBlock | None:
        """Which block a character offset falls in — how a Phase 3 evidence span
        finds the section it was extracted from."""
        for block in self.blocks:
            if block.start_char <= offset < block.end_char:
                return block
        return None

    def spans_resolve(self) -> bool:
        """The invariant this module exists to hold, as a checkable predicate.

        Cheap enough to assert in tests and in the ingestion path; a revision
        whose spans do not resolve must not be stored.
        """
        return all(
            self.text[block.start_char : block.end_char] == block.text for block in self.blocks
        )


class BlockBuilder:
    """Accumulates blocks and the canonical text together, so offsets are exact.

    Every parser (plain text, HTML, and whatever a later source needs) goes
    through this. That is deliberate: the offset arithmetic is the part worth
    getting right once, and a second parser reimplementing it is how the two
    drift apart.
    """

    def __init__(self, max_chars: int = MAX_DOCUMENT_CHARS):
        self._max_chars = max_chars
        self._parts: list[str] = []
        self._blocks: list[ParsedBlock] = []
        self._length = 0
        self._truncated = False

    def add(self, text: str, block_type: BlockType) -> ParsedBlock | None:
        """Append one block. Returns None when the text was empty after
        normalization, or when the document ceiling has been reached."""
        if self._truncated:
            return None

        normalized = normalize_block_text(text)
        if not normalized:
            return None

        separator = BLOCK_SEPARATOR if self._parts else ""
        start = self._length + len(separator)
        end = start + len(normalized)
        if end > self._max_chars:
            self._truncated = True
            return None

        self._parts.append(separator + normalized)
        self._length = end
        block = ParsedBlock(
            ordinal=len(self._blocks),
            block_type=block_type,
            text=normalized,
            start_char=start,
            end_char=end,
        )
        self._blocks.append(block)
        return block

    def build(self) -> ParsedDocument:
        warnings = []
        if self._truncated:
            warnings.append(
                f"document exceeded {self._max_chars} characters and was truncated; "
                "later blocks were not parsed"
            )
        document = ParsedDocument(
            text="".join(self._parts),
            blocks=list(self._blocks),
            truncated=self._truncated,
            warnings=warnings,
        )
        # Cheap, and the one thing that must never be wrong. A parser bug that
        # broke offsets would otherwise surface much later as a misquoted
        # evidence span, with nothing pointing back here.
        if not document.spans_resolve():  # pragma: no cover - guards a builder bug
            raise AssertionError("block offsets do not resolve against the built text")
        return document


def parse_plain_text(raw: str) -> ParsedDocument:
    """Blocks from plain text: bullet lines become list items, runs of ordinary
    lines become paragraphs.

    Deliberately no heading detection. Plain text signals a heading only through
    convention — a short line, a trailing colon, capitalisation — and guessing
    from those is inference about meaning, not structure. HTML says `<h2>` and
    gets a heading; a `.txt` CV does not, and pretending otherwise would put a
    wrong section label on a Phase 3 requirement.
    """
    builder = BlockBuilder()
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            builder.add(" ".join(paragraph), BlockType.PARAGRAPH)
            paragraph.clear()

    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            flush()
        elif looks_like_list_item(line):
            flush()
            builder.add(line, BlockType.LIST_ITEM)
        else:
            paragraph.append(line.strip())
    flush()

    return builder.build()
