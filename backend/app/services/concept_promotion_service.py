"""Turning a reviewed unknown mention into a concept the linker can find.

This is the second half of spec 9.4, and the half that was missing. The first
half — count unknown mentions, never create a concept automatically, make a
person decide — has been running since the taxonomy landed. Without this, that
decision went into a column nothing read: `promoted` and `ignored` differed only
in the word stored, and the term came back unmapped on the very next vacancy.

What promotion is allowed to be is narrow on purpose. It creates one concept,
with the words the documents actually used as its labels, under a provisional
type, in a namespace that says plainly it was not imported from anywhere. It
does not guess a parent, a definition, or which ESCO branch the term belongs
under. 9.4 asks for those as *optional* links, and inventing them here would
put unreviewed structure into the taxonomy under the cover of a review.
"""

import logging
import uuid
from dataclasses import dataclass

from app.repositories.mention_repository import MentionRepository
from app.repositories.taxonomy_repository import TaxonomyRepository

logger = logging.getLogger(__name__)

PROMOTED = "promoted"
IGNORED = "ignored"
PENDING = "pending"


@dataclass(frozen=True)
class ReviewOutcome:
    normalized_text: str
    status: str
    # Set only when a promotion created or found a concept. Null for `ignored`
    # and `pending`, which are decisions about a term, not about the taxonomy.
    concept_id: str | None = None
    created: bool = False


class ConceptPromotionService:
    def __init__(self, taxonomy: TaxonomyRepository, mentions: MentionRepository):
        self._taxonomy = taxonomy
        self._mentions = mentions

    async def review(self, normalized_text: str, status: str) -> ReviewOutcome | None:
        """Record a decision, and act on it when the decision is `promoted`.

        Returns None when there is no such term, so the caller can answer 404
        rather than reporting a decision about nothing.
        """
        term = await self._mentions.get_unmapped(normalized_text)
        if term is None:
            return None

        concept_id: uuid.UUID | None = None
        created = False
        if status == PROMOTED:
            concept_id, created = await self._promote(normalized_text, term.sample_raw_text)
        elif term.promoted_concept_id is not None:
            # Undoing a promotion has to undo what the promotion did. Clearing
            # the column alone would leave the concept in the alias index, so
            # the term would keep linking to something the reviewer just said
            # should not exist — the one state a review screen must not produce.
            await self._taxonomy.retire_internal_concept(term.promoted_concept_id)

        await self._mentions.review_unmapped(normalized_text, status, concept_id=concept_id)
        return ReviewOutcome(
            normalized_text=normalized_text,
            status=status,
            concept_id=str(concept_id) if concept_id else None,
            created=created,
        )

    async def _promote(self, normalized_text: str, sample_raw_text: str) -> tuple[uuid.UUID, bool]:
        """Create the concept, or return the one this term already has.

        Idempotent because the identity is derived from the term itself.
        Promoting the same word twice — two people, a double click, a re-run of
        a bulk review — must not leave the index holding two concepts with the
        same label, which is exactly the `ambiguous` state 9.3 reserves for a
        real ambiguity.
        """
        existing = await self._taxonomy.internal_concept_by_term(normalized_text)
        if existing is not None:
            return existing, False

        concept_id = await self._taxonomy.create_internal_concept(
            normalized_text=normalized_text,
            # The form a person would recognise, not the normalized one: the
            # queue shows "CI/CD" and the concept should say "CI/CD", while the
            # index matches on the normalized form either way.
            preferred_label=sample_raw_text,
            forms=[normalized_text, sample_raw_text],
        )
        logger.info("promoted %r to internal concept %s", sample_raw_text, concept_id)
        return concept_id, True

    async def review_many(self, decisions: list[tuple[str, str]]) -> list[ReviewOutcome]:
        """Several decisions in one transaction.

        Bulk exists because of the shape of the real queue rather than as a
        convenience: 11 504 terms, of which the top hundred are 16% of
        occurrences, so there is no short prefix a person can review and stop.
        One at a time is not a workflow anybody finishes.
        """
        outcomes: list[ReviewOutcome] = []
        for normalized_text, status in decisions:
            outcome = await self.review(normalized_text, status)
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes
