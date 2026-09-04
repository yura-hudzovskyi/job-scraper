"""Use case: link the terms in a document to the active taxonomy.

Runs right after extraction, on the same document text, and writes one
`profile_concept_mentions` row per distinct term found — linked, ambiguous or
unmapped, with the span it was found at.

**Why this is not async, when spec 9.5 says linking should be.** That section
budgets the *embedding and cross-encoder* stage, which at 600 candidate pairs
per document would dominate ingestion — the Phase 0 benchmark put one GLiNER2
pass at 3 s and made that concrete. The lexical stage measured here is a
dictionary lookup per word: 0.5 ms for a whole vacancy. Deferring something that
cheap would cost more in machinery than it saves. When the reranking stage
arrives it goes on the outbox; this does not need to.

The alias index is cached per taxonomy version, which is the other thing 9.5
asks for. Building it reads the whole release, and a version only changes on
import, so a process builds it once and reuses it until a new release is
activated.
"""

import logging
import uuid
from dataclasses import dataclass

from app.domain.taxonomy.linking import AliasIndex, LinkStatus, deduplicate, find_mentions
from app.integrations.taxonomy import esco
from app.repositories.mention_repository import MentionRepository
from app.repositories.taxonomy_repository import TaxonomyRepository

logger = logging.getLogger(__name__)

# version id -> index. Keyed by the version rather than the namespace so that
# activating a new release invalidates it by simply not matching.
_INDEX_CACHE: dict[uuid.UUID, AliasIndex] = {}


def clear_index_cache() -> None:
    """Drop the cached indexes. Used by tests, and by an import that wants the
    next link to see its release without waiting for a process restart."""
    _INDEX_CACHE.clear()


@dataclass(frozen=True)
class LinkingResult:
    linked: int = 0
    ambiguous: int = 0
    unmapped: int = 0
    skipped_reason: str | None = None

    @property
    def total(self) -> int:
        return self.linked + self.ambiguous + self.unmapped


class ConceptLinkingService:
    def __init__(
        self,
        taxonomy_repository: TaxonomyRepository,
        mention_repository: MentionRepository,
        namespace: str = esco.NAMESPACE,
    ):
        self._taxonomy = taxonomy_repository
        self._mentions = mention_repository
        self._namespace = namespace

    async def link(self, profile_revision_id: uuid.UUID, text: str) -> LinkingResult:
        """Find and store every taxonomy term in this document's text."""
        if not text.strip():
            return LinkingResult(skipped_reason="the document has no text")

        version = await self._taxonomy.active_version(self._namespace)
        if version is None:
            # No release imported yet. Not an error: the pipeline ran without a
            # taxonomy for its whole life before this phase, and says so rather
            # than failing extraction.
            return LinkingResult(skipped_reason=f"no active {self._namespace} taxonomy")

        index = await self._index_for(version.id, version.version)
        mentions = deduplicate(find_mentions(text, index))
        if not mentions:
            return LinkingResult()

        rows = []
        unmapped_terms = []
        counts = {LinkStatus.LINKED: 0, LinkStatus.AMBIGUOUS: 0, LinkStatus.UNMAPPED: 0}
        for mention in mentions:
            status = mention.link_status
            counts[status] += 1
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "profile_revision_id": profile_revision_id,
                    # Only a single unambiguous match sets the foreign key; the
                    # alternatives of an ambiguous mention are kept in metadata
                    # rather than one of them being promoted to the truth.
                    "concept_id": mention.concept_id
                    if status == LinkStatus.LINKED
                    else None,
                    "raw_text": mention.raw_text,
                    "normalized_text": mention.normalized_text,
                    "role": "required",
                    "link_status": status,
                    "extraction_confidence": 1.0,
                    "link_score": mention.specificity,
                    "start_char": mention.start_char,
                    "end_char": mention.end_char,
                    "metadata": (
                        {"alternatives": [str(c) for c in mention.concept_ids]}
                        if status == LinkStatus.AMBIGUOUS
                        else None
                    ),
                }
            )
            if status == LinkStatus.UNMAPPED:
                unmapped_terms.append((mention.normalized_text, mention.raw_text))

        await self._mentions.replace_for_profile(profile_revision_id, rows)
        await self._mentions.record_unmapped(unmapped_terms)

        return LinkingResult(
            linked=counts[LinkStatus.LINKED],
            ambiguous=counts[LinkStatus.AMBIGUOUS],
            unmapped=counts[LinkStatus.UNMAPPED],
        )

    async def _index_for(self, version_id: uuid.UUID, version: str) -> AliasIndex:
        cached = _INDEX_CACHE.get(version_id)
        if cached is not None:
            return cached
        forms = await self._taxonomy.surface_forms(self._namespace, version)
        index = AliasIndex.build(forms)
        _INDEX_CACHE.clear()  # only ever one release is active
        _INDEX_CACHE[version_id] = index
        logger.info(
            "built alias index for %s %s: %d forms", self._namespace, version, len(index)
        )
        return index
