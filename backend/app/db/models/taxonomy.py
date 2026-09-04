"""ORM tables for the imported taxonomy and the mentions linked against it.

ESCO is imported from a pinned release rather than queried live (spec 9.1), so
these tables are a local copy with a version stamped on every row. Two versions
coexist on purpose: a profile revision extracted under v1.2.0 has to stay
readable after v1.2.1 is imported, and the only way that works is if the older
concepts are still there to point at.

`taxonomy_versions` is what makes an import atomic. Rows land under a version
that is `importing`, get counted and checked, and only then does that version
become `active` — so a half-finished import is never something the linker can
see.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class TaxonomyVersionModel(UUIDPrimaryKeyMixin, Base):
    """One import of one taxonomy release.

    `source_checksum` is the reason this table exists rather than a bare version
    string: "we imported v1.2.1" is not reproducible, while "we imported the file
    with this sha256" is. A re-import of the same checksum is a no-op.
    """

    __tablename__ = "taxonomy_versions"
    __table_args__ = (
        UniqueConstraint("namespace", "version", name="uq_taxonomy_versions_namespace_version"),
        CheckConstraint(
            "status IN ('importing', 'ready', 'active', 'superseded', 'failed')",
            name="ck_taxonomy_versions_status",
        ),
    )

    # "esco" | "onet" | "internal"
    namespace: Mapped[str]
    # The release identifier as the publisher names it, e.g. "1.2.1".
    version: Mapped[str]
    source_checksum: Mapped[str | None] = mapped_column(default=None)
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list)

    concept_count: Mapped[int] = mapped_column(default=0)
    relation_count: Mapped[int] = mapped_column(default=0)

    # importing -> ready -> active; a previously active version becomes
    # superseded. `failed` keeps a broken import visible instead of leaving a
    # half-populated version that looks importable.
    status: Mapped[str] = mapped_column(default="importing", server_default="importing")
    failure_detail: Mapped[str | None] = mapped_column(default=None)

    imported_at: Mapped[datetime] = mapped_column(server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(default=None)


class TaxonomyConceptModel(UUIDPrimaryKeyMixin, Base):
    """One skill, knowledge item or occupation from the imported release.

    `labels` holds every surface form the publisher gives, keyed by language:
    `{"en": ["Python", "Python programming"], "uk": [...]}`. ESCO's CSVs are
    monolingual — one file per language — so a multilingual import merges several
    files into this one column rather than storing a row per language.

    That column is what the alias linker matches against, so its shape is
    load-bearing rather than a convenience.
    """

    __tablename__ = "taxonomy_concepts"
    __table_args__ = (
        UniqueConstraint(
            "namespace", "external_id", "taxonomy_version", name="uq_taxonomy_concepts_identity"
        ),
        Index("ix_taxonomy_concepts_version_status", "taxonomy_version", "status"),
    )

    namespace: Mapped[str]
    # ESCO's conceptUri, e.g. http://data.europa.eu/esco/skill/<uuid>.
    external_id: Mapped[str]
    taxonomy_version: Mapped[str]

    # "skill" | "knowledge" | "occupation" — ESCO's own skillType, normalized.
    concept_type: Mapped[str]
    preferred_label: Mapped[str]
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    description: Mapped[str | None] = mapped_column(default=None)

    status: Mapped[str] = mapped_column(default="active", server_default="active")


class TaxonomyRelationModel(Base):
    """An edge between two concepts in one imported version.

    Both ends are concept ids rather than URIs, so a relation cannot outlive the
    concepts it joins, and a version's graph is deleted with its concepts.
    """

    __tablename__ = "taxonomy_relations"
    __table_args__ = (
        PrimaryKeyConstraint(
            "source_concept_id", "target_concept_id", "relation_type", name="pk_taxonomy_relations"
        ),
        CheckConstraint(
            "relation_type IN ('broader', 'narrower', 'related', 'essential_for', "
            "'optional_for', 'same_as')",
            name="ck_taxonomy_relations_type",
        ),
    )

    source_concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_concepts.id"), index=True
    )
    target_concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_concepts.id"), index=True
    )
    relation_type: Mapped[str]


class ProfileConceptMentionModel(UUIDPrimaryKeyMixin, Base):
    """A term found in a document, and what it was linked to — if anything.

    The raw mention and its offsets are kept whether or not a concept was found,
    which is the whole point: `unmapped` is a real answer (spec 9.3), and an
    unlinked mention is still evidence that the word appeared, which the lexical
    channel matches on.

    The evidence span is stored inline rather than as a foreign key to an
    `evidence_spans` table. That table is in spec 7.3 but does not exist yet —
    evidence currently lives inside the profile JSONB — and inventing a second
    home for spans before there is a reader for it would be the parallel
    mechanism 24.0 warns against.
    """

    __tablename__ = "profile_concept_mentions"
    __table_args__ = (
        CheckConstraint(
            "link_status IN ('linked', 'ambiguous', 'unmapped', 'manual')",
            name="ck_profile_concept_mentions_link_status",
        ),
        CheckConstraint(
            "link_status <> 'linked' OR concept_id IS NOT NULL",
            name="ck_profile_concept_mentions_linked_has_concept",
        ),
        CheckConstraint(
            "end_char IS NULL OR start_char IS NULL OR end_char > start_char",
            name="ck_profile_concept_mentions_span_ordered",
        ),
        Index("ix_profile_concept_mentions_normalized", "normalized_text"),
    )

    profile_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile_revisions.id"), index=True
    )
    concept_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_concepts.id"), default=None, index=True
    )

    raw_text: Mapped[str]
    # Case-folded and whitespace-collapsed — what the linker looks up, and what
    # groups repeated mentions of the same term across the corpus.
    normalized_text: Mapped[str]
    category: Mapped[str | None] = mapped_column(default=None)
    # held | target | required | preferred | responsibility | domain
    role: Mapped[str] = mapped_column(default="required", server_default="required")

    link_status: Mapped[str] = mapped_column(default="unmapped", server_default="unmapped")
    extraction_confidence: Mapped[float] = mapped_column(default=1.0, server_default="1.0")
    link_score: Mapped[float | None] = mapped_column(default=None)

    start_char: Mapped[int | None] = mapped_column(default=None)
    end_char: Mapped[int | None] = mapped_column(default=None)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, default=None)


class UnmappedMentionModel(Base):
    """A term the linker could not place, aggregated across the corpus.

    Spec 9.4: an unknown mention must not silently become a new concept. It is
    counted here, and a person decides whether it is a real term worth adding or
    noise worth ignoring. Frequency is the whole value — a term seen once is
    probably a typo, one seen four hundred times is a gap in the taxonomy.
    """

    __tablename__ = "unmapped_mentions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'promoted', 'ignored')", name="ck_unmapped_mentions_status"
        ),
        Index("ix_unmapped_mentions_status_occurrences", "status", "occurrences"),
    )

    normalized_text: Mapped[str] = mapped_column(primary_key=True)
    sample_raw_text: Mapped[str]
    occurrences: Mapped[int] = mapped_column(default=1, server_default="1")

    status: Mapped[str] = mapped_column(default="pending", server_default="pending")
    reviewed_at: Mapped[datetime | None] = mapped_column(default=None)
    # Set when a review promotes this into the taxonomy as an internal concept.
    promoted_concept_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_concepts.id"), default=None
    )

    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
