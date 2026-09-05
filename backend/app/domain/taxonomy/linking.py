"""Finding taxonomy terms in a document, and deciding what they link to.

This is the lexical half of spec 9.3 — "add lexical candidates from preferred
and non-preferred labels" — and it needs no model, which is why it ships before
the embedding stage. With 135 242 ESCO surface forms indexed, a vacancy's text
can be matched against the taxonomy directly.

It is not the keyword rule 25.3 forbids. That prohibition is on occupations and
professional meaning *hardcoded in application code*; these labels come from a
versioned external release, live in a table, and change when the release does.
The distinction is the same one that separates `blocked_stack` matching, which
the app already does, from `if "senior" in title`.

**Matching is by whole words, longest first.** Scanning for substrings would
find "r" inside "your" and "ml" inside "html"; tokenising the document and
looking up word n-grams cannot. Where two labels overlap — "machine learning"
and "learning" — the longer wins, because the more specific concept is the one
the author meant.

**Very short forms are dropped.** ESCO legitimately lists `r`, `ml`, `ux`, `io`,
`bi`, `ng` and `pi` as alternative labels, and every one of them matches noise in
a real vacancy. Below MIN_FORM_CHARS a form is not indexed at all. That loses
standalone "ML" and "UX" — they still link through "machine learning" and "user
experience" — and it follows 9.3's rule that a correct `unmapped` beats a wrong
concept id.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.integrations.taxonomy.esco import normalize_label

# Below this, a single-word label matches noise more often than it matches its
# concept. Three keeps SQL, AWS, PHP, CSS and Git; two would re-admit `ml`, `ux`,
# `io`, `bi` and `ng`, which is where the damage is.
MIN_FORM_CHARS = 3

# How fast a word's specificity decays as more of the taxonomy's labels use it.
# Measured on ESCO v1.2.1 by counting the labels each word appears in:
#
#     docker 0   postgresql 1   python 3   english 41
#     knowledge 168   learning 219   design 1334   management 1390
#
# The first group names things; the second is ordinary English that happens to
# be listed as an alternative label. A vacancy saying "design" almost never
# means ESCO's "think creatively", and "Knowledge of Python" is not a mention of
# the concept "knowledge".
SPECIFICITY_DECAY = 20.0

# A single-word match below this is recorded as `unmapped` rather than linked —
# spec 9.3 step 5. With the decay above this keeps `english` (0.33) and drops
# `knowledge` (0.11) and `design` (0.02). It is a measured cut, not a guess, and
# it is the crude stand-in for the reranking stage that supersedes it once
# ml-service exists.
MIN_SPECIFICITY = 0.2

# Longest label worth assembling from the document. Measured on ESCO v1.2.1: six
# words covers 92% of surface forms, and the index reports its own maximum, so
# this is only a ceiling on the work per document rather than a guess.
MAX_PHRASE_WORDS = 8

_WORD = re.compile(r"\w+", re.UNICODE)


class LinkStatus:
    LINKED = "linked"
    AMBIGUOUS = "ambiguous"
    UNMAPPED = "unmapped"


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class MentionCandidate:
    """A taxonomy term found in a document, with where it was found.

    `concept_ids` empty means the phrase was recognised as a candidate but
    matched nothing — that never reaches storage from this module, since only
    matched phrases are emitted. It is the caller's mention that can be
    `unmapped`.
    """

    raw_text: str
    normalized_text: str
    start_char: int
    end_char: int
    concept_ids: list[uuid.UUID] = field(default_factory=list)
    # How much this form belongs to its concept rather than to the language.
    # 1.0 for any phrase; lower for a single word the taxonomy reuses widely.
    specificity: float = 1.0

    @property
    def link_status(self) -> str:
        if self.specificity < MIN_SPECIFICITY:
            # The word matched, but it is ordinary language rather than a term.
            # Spec 9.3 is explicit that a correct `unmapped` beats a wrong id.
            return LinkStatus.UNMAPPED
        if len(self.concept_ids) == 1:
            return LinkStatus.LINKED
        if len(self.concept_ids) > 1:
            # Spec 9.3 step 6: too close to call is a stored outcome, not a coin
            # flip. 1 955 ESCO forms are shared by several concepts — "geologist"
            # by six — and picking one would be inventing a fact.
            return LinkStatus.AMBIGUOUS
        return LinkStatus.UNMAPPED

    @property
    def concept_id(self) -> uuid.UUID | None:
        """The single concept this resolves to, or None when it does not."""
        return self.concept_ids[0] if len(self.concept_ids) == 1 else None


class AliasIndex:
    """Normalized surface form to the concepts that use it.

    Built once per taxonomy version and reused across documents: assembling it
    reads every concept in the release, and doing that per document would cost
    more than the matching it enables. Spec 9.5 asks for exactly this kind of
    caching, for exactly this reason.
    """

    def __init__(
        self,
        entries: dict[str, list[uuid.UUID]] | None = None,
        specificity: dict[str, float] | None = None,
    ):
        self._entries: dict[str, list[uuid.UUID]] = entries or {}
        self._specificity: dict[str, float] = specificity or {}
        self._max_words = self._longest(self._entries)

    @staticmethod
    def _longest(entries: dict[str, list[uuid.UUID]]) -> int:
        return min(
            MAX_PHRASE_WORDS,
            max((len(form.split()) for form in entries), default=1),
        )

    @classmethod
    def build(cls, concepts: Iterable[tuple[uuid.UUID, Iterable[str]]]) -> "AliasIndex":
        """Index (concept id, surface forms) pairs.

        Specificity is derived from the same labels being indexed rather than
        from any outside word list: a word the taxonomy reuses across a thousand
        labels is ordinary language, one it uses in three is a term. That keeps
        this free of hardcoded language knowledge, which is the line 25.3 draws.
        """
        entries: dict[str, list[uuid.UUID]] = {}
        component_counts: dict[str, int] = {}

        for concept_id, forms in concepts:
            for form in forms:
                normalized = normalize_label(form)
                for word in set(normalized.split()):
                    component_counts[word] = component_counts.get(word, 0) + 1
                if len(normalized) < MIN_FORM_CHARS:
                    continue
                known = entries.setdefault(normalized, [])
                if concept_id not in known:
                    known.append(concept_id)

        specificity = {
            form: 1.0 / (1.0 + component_counts.get(form, 0) / SPECIFICITY_DECAY)
            for form in entries
            if " " not in form
        }
        return cls(entries, specificity)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def max_words(self) -> int:
        return self._max_words

    def lookup(self, normalized_phrase: str) -> list[uuid.UUID]:
        return self._entries.get(normalized_phrase, [])

    def specificity_of(self, normalized_phrase: str) -> float:
        """A phrase is specific by construction; a single word is only as
        specific as the taxonomy's use of it allows."""
        return self._specificity.get(normalized_phrase, 1.0)


def tokenize(text: str) -> list[Token]:
    """Words and where they are, so a match can be reported as a span.

    Offsets come from the match rather than from re-searching the text, for the
    same reason block offsets do in Phase 2: an offset that was searched for can
    find the wrong occurrence.
    """
    return [Token(match.group(), match.start(), match.end()) for match in _WORD.finditer(text)]


def find_mentions(text: str, index: AliasIndex) -> list[MentionCandidate]:
    """Every taxonomy term in the text, longest match first, no overlaps.

    Walks left to right; at each position tries the longest phrase the index
    could contain and works down. A phrase that matches consumes its tokens, so
    "machine learning" is one mention rather than two overlapping ones.
    """
    tokens = tokenize(text)
    normalized = [normalize_label(token.text) for token in tokens]
    mentions: list[MentionCandidate] = []

    position = 0
    while position < len(tokens):
        matched_length = 0
        for length in range(min(index.max_words, len(tokens) - position), 0, -1):
            phrase = " ".join(normalized[position : position + length])
            concept_ids = index.lookup(phrase)
            if not concept_ids:
                continue
            start = tokens[position].start
            end = tokens[position + length - 1].end
            mentions.append(
                MentionCandidate(
                    raw_text=text[start:end],
                    normalized_text=phrase,
                    start_char=start,
                    end_char=end,
                    concept_ids=list(concept_ids),
                    specificity=index.specificity_of(phrase),
                )
            )
            matched_length = length
            break
        position += matched_length or 1

    return mentions


@dataclass(frozen=True)
class ExtractedSpan:
    """A competency a model found, before the taxonomy has had a look at it.

    Deliberately not `ConceptMention` from the profile schema: this module knows
    about taxonomies and offsets, and importing the profile shape here would tie
    the linker to the extractor's vocabulary.
    """

    raw_text: str
    start_char: int
    end_char: int
    confidence: float = 1.0


def link_spans(spans: Sequence[ExtractedSpan], index: AliasIndex) -> list[MentionCandidate]:
    """Match phrases a model already identified as competencies, not every n-gram.

    This is spec 9.3 read literally — "for every extracted mention" — and the
    difference from `find_mentions` is what it does *not* look at. Scanning a
    whole vacancy asks the taxonomy about every word in it, so "skills",
    "environment" and "communication" arrive as candidate terms because ESCO
    happens to contain labels using those words. The model has already decided
    which phrases are competencies; the taxonomy's job is only to say which
    concept each one is.

    Two attempts per span, and no third. The whole phrase first, because
    "Google Sheets" is a better answer than "Google". Failing that, the known
    forms inside it, with offsets shifted back into the document so the evidence
    still points where the phrase actually is. If neither matches it is
    `unmapped` — never forced onto a nearby concept (9.3).
    """
    linked: list[MentionCandidate] = []
    for span in spans:
        normalized = normalize_label(span.raw_text)
        concept_ids = index.lookup(normalized)
        if concept_ids:
            linked.append(
                MentionCandidate(
                    raw_text=span.raw_text,
                    normalized_text=normalized,
                    start_char=span.start_char,
                    end_char=span.end_char,
                    concept_ids=list(concept_ids),
                    specificity=index.specificity_of(normalized),
                )
            )
            continue

        inner = find_mentions(span.raw_text, index)
        if inner:
            linked.extend(
                MentionCandidate(
                    raw_text=found.raw_text,
                    normalized_text=found.normalized_text,
                    start_char=span.start_char + found.start_char,
                    end_char=span.start_char + found.end_char,
                    concept_ids=found.concept_ids,
                    specificity=found.specificity,
                )
                for found in inner
            )
            continue

        linked.append(
            MentionCandidate(
                raw_text=span.raw_text,
                normalized_text=normalized,
                start_char=span.start_char,
                end_char=span.end_char,
                concept_ids=[],
            )
        )
    return linked


def deduplicate(mentions: Sequence[MentionCandidate]) -> list[MentionCandidate]:
    """One mention per distinct term, keeping its first occurrence.

    A vacancy repeats "Python" five times; that is one fact about the vacancy,
    not five. The first occurrence is kept so the evidence span points at where
    the reader will look first.
    """
    seen: set[str] = set()
    unique: list[MentionCandidate] = []
    for mention in mentions:
        if mention.normalized_text in seen:
            continue
        seen.add(mention.normalized_text)
        unique.append(mention)
    return unique
