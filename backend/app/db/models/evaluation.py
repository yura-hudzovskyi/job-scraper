"""ORM table for judged candidate-vacancy pairs — spec 20.1.

The row is a judgement, not a score. Nothing the system computes belongs in
`label`: that column exists to be compared *against* what the system computes,
and a value written by the pipeline would make every metric measured against it
a tautology.

`system_score` is the exception and is deliberately named as one. It records
what the ranker said at the moment the pair was sampled, which is how a biased
sample stays visible later — pairs drawn from the top of a ranking tell you
little about what the ranking missed, and 20.1 accepts that shortcut only
because it is written down.

Identity is (candidate revision, canonical job). The vacancy revision is stored
alongside rather than as the key: the ranker ranks canonical jobs, so that is
the unit a metric is computed over, while the revision says which text a person
actually read. When a vacancy is re-scraped into a new revision, the judgement
is still about the text that was judged, and the two columns together are what
makes a stale label detectable instead of silently wrong.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class EvaluationPairModel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_pairs"
    __table_args__ = (
        UniqueConstraint(
            "candidate_revision_id",
            "canonical_job_id",
            name="uq_evaluation_pairs_candidate_job",
        ),
        CheckConstraint(
            "label IS NULL OR label BETWEEN 0 AND 3", name="ck_evaluation_pairs_label_range"
        ),
        CheckConstraint(
            "(label IS NULL) = (annotated_at IS NULL)",
            name="ck_evaluation_pairs_label_and_time_agree",
        ),
        CheckConstraint("tier IN ('seed', 'core', 'full')", name="ck_evaluation_pairs_tier"),
        Index("ix_evaluation_pairs_tier_label", "tier", "label"),
    )

    candidate_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_revisions.id"), index=True
    )
    canonical_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canonical_jobs.id"))
    # The vacancy text that was read. Nullable because a pair can be sampled
    # from a canonical job whose revision was purged by retention before anyone
    # judged it — the pair is then unjudgeable and says so, rather than pointing
    # at a row that no longer exists.
    job_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_revisions.id"), default=None
    )

    # 0 irrelevant, 1 weak, 2 relevant, 3 strong (spec 20.1). NULL means nobody
    # has judged it yet, which is the state most pairs are in most of the time.
    label: Mapped[int | None] = mapped_column(default=None)
    annotator: Mapped[str | None] = mapped_column(default=None)
    annotated_at: Mapped[datetime | None] = mapped_column(default=None)

    # seed (300) -> core (1 200) -> full (3 000+), per 20.1's tier table.
    tier: Mapped[str] = mapped_column(default="seed", server_default="seed")
    # Why this pair is in the set. A sample drawn only from what the ranker
    # already liked cannot measure what it missed, so the strategy travels with
    # the row instead of living in whoever ran the sampler's memory.
    sampled_from: Mapped[str] = mapped_column(default="unknown", server_default="unknown")
    # The 0-100 score the ranker gave this pair when it was sampled. Never an
    # input to a metric — it is what the metric is checked against, and what
    # makes selection bias legible.
    system_score: Mapped[float | None] = mapped_column(default=None)

    notes: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
