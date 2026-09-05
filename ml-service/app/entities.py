"""Turning what the model returned into something a caller can trust.

Three things happen here, and all three are about the offsets rather than about
the entities. Spec 3.5.2 condition 1 is that no extracted value exists without an
exact offset into the document revision, and an offset is only worth as much as
the check behind it: `text[start:end]` either is the entity's own text or the
whole evidence chain is quietly wrong, in range and pointing at the wrong words.

Deliberately free of torch, so the rules can be tested without loading 1.2 GB of
weights.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Entity:
    """One mention the model found, located exactly in the text it was given."""

    label: str
    text: str
    start_char: int
    end_char: int
    confidence: float


@dataclass(frozen=True)
class DocumentEntities:
    entities: list[Entity]
    # Spans the model returned that did not quote the text at their own offsets.
    # Counted rather than silently dropped: a model or tokeniser change that
    # starts producing them turns into a visible number instead of a slow
    # decline in extraction quality nobody can point at.
    rejected_spans: int
    truncated: bool


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Cut a document to what the model will be given, and say whether it was cut.

    The caller needs the flag because the offsets it gets back index into the
    truncated string. They stay valid against the original only because the cut
    is at the end — a change to cutting from the middle would invalidate every
    offset after the cut, silently.
    """
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def collect(raw: dict[str, object], text: str) -> tuple[list[Entity], int]:
    """Read GLiNER2's nested output into flat, verified entities.

    The shape is `{"entities": {label: [{text, confidence, start, end}, ...]}}`
    when the model is asked for spans and confidence. Without those two flags it
    returns bare strings, which is why the extractor always passes them: a bare
    string cannot be located in a document that mentions "Python" three times.
    """
    entities: list[Entity] = []
    rejected = 0

    groups = raw.get("entities")
    if not isinstance(groups, dict):
        return entities, rejected

    for label, found in groups.items():
        if not isinstance(found, list):
            continue
        for item in found:
            if not isinstance(item, dict):
                # A bare string means include_spans did not take effect. There
                # is nothing to locate it by, so it cannot become evidence.
                rejected += 1
                continue
            start, end = item.get("start"), item.get("end")
            span_text = item.get("text")
            if not isinstance(start, int) or not isinstance(end, int):
                rejected += 1
                continue
            if not isinstance(span_text, str) or text[start:end] != span_text:
                rejected += 1
                continue
            confidence = item.get("confidence")
            entities.append(
                Entity(
                    label=str(label),
                    text=span_text,
                    start_char=start,
                    end_char=end,
                    confidence=float(confidence) if isinstance(confidence, int | float) else 0.0,
                )
            )
    return entities, rejected


def best_label_per_span(entities: list[Entity]) -> list[Entity]:
    """One entity per span, under the label the model was most sure of.

    The model scores every label against every span independently, so the same
    characters come back more than once: "Python" arrives as `technology` at
    0.97 and as `tool` at 0.66. Both are the same mention of the same word, and
    passing both on would double-count it everywhere downstream.

    Overlapping but different spans are left alone — "Apache Kafka" and "Kafka"
    are two spans, and deciding which one to keep needs the taxonomy, which is
    the linker's job and not this service's.
    """
    best: dict[tuple[int, int], Entity] = {}
    for entity in entities:
        span = (entity.start_char, entity.end_char)
        current = best.get(span)
        if current is None or entity.confidence > current.confidence:
            best[span] = entity
    # Document order, so the same input always produces the same output (2.6) —
    # dict order here would follow whatever order the labels came back in.
    return sorted(best.values(), key=lambda e: (e.start_char, e.end_char))
