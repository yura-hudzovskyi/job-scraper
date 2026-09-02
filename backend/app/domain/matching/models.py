"""Match result shapes.

A score here is never a verdict handed down: `score` is always reproducible from
`similarity`, `relevance` and the weight, all three of which are stored and shown.
If a job was never reranked, `relevance` is None and the UI says "not reranked"
rather than implying a reranker agreed.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Recommendation(StrEnum):
    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


class MatchDecision(StrEnum):
    """The user's own verdict, set from the Telegram Approve/Reject buttons.
    Independent of `Recommendation` (the pipeline's opinion) and never overwritten
    by a re-match."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class JobMatch:
    user_id: str
    canonical_job_id: str

    # Ineligible means a hard filter the user configured rejected it; the reasons
    # name which one. An ineligible job is still stored, so "why isn't this in my
    # list" always has an answer.
    eligible: bool
    filter_reasons: list[str] = field(default_factory=list)

    score: float = 0.0
    similarity: float = 0.0
    relevance: float | None = None
    rerank_position: int | None = None

    recommendation: Recommendation = Recommendation.SKIP
    embedding_model: str | None = None
    rerank_model: str | None = None
    rerank_weight: float | None = None

    id: str = ""
    scored_at: datetime | None = None
    decision: MatchDecision = MatchDecision.PENDING
