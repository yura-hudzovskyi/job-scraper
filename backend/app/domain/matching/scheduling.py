"""Which matches are worth an LLM call today — see docs/ai-pipeline-v3.md (F3).

The old rule was "every CONSIDER or APPLY match", which spends the day's budget
in scrape order: a hundred obvious middling matches get analysed and the one job
sitting exactly on the apply/consider line doesn't, because it was scraped later.

This ranks by value of information — where a second opinion could actually change
what the user does:

- **Near a decision boundary.** A 74 and a 76 lead to different advice, and that
  is precisely where the deterministic score is least trustworthy.
- **The methods disagree.** Requirement coverage says one thing and semantic /
  rerank relevance says another; one of them is wrong and a reader can tell which.
- **Low confidence.** The pipeline says it couldn't establish much. An LLM reading
  the actual text can.
- **High score.** These are the ones the user will act on, so being right about
  them matters most.

A match that already has an LLM verdict is not a candidate at all: re-analysing
it spends budget to produce the answer already stored.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.matching.models import JobMatch, Recommendation

APPLY_BOUNDARY = 75.0
CONSIDER_BOUNDARY = 55.0

WEIGHT_BOUNDARY = 0.40
WEIGHT_DISAGREEMENT = 0.25
WEIGHT_UNCERTAINTY = 0.20
WEIGHT_SCORE = 0.15

# Past this distance from a boundary, the score is not going to be flipped by a
# second opinion.
_BOUNDARY_RANGE = 20.0


class EnrichmentReason(StrEnum):
    """Why this match was picked — recorded so the ordering can be explained
    rather than trusted."""

    DECISION_BOUNDARY = "decision_boundary"
    HIGH_DISAGREEMENT = "high_disagreement"
    LOW_CONFIDENCE = "low_confidence"
    TOP_RANKED = "top_ranked"


@dataclass(frozen=True)
class EnrichmentCandidate:
    match: JobMatch
    priority: float
    reason: EnrichmentReason


def _boundary_proximity(score: float) -> float:
    distance = min(abs(score - APPLY_BOUNDARY), abs(score - CONSIDER_BOUNDARY))
    return max(0.0, 1.0 - distance / _BOUNDARY_RANGE)


def _disagreement(match: JobMatch) -> float:
    """How far apart the evidence-based and the similarity-based views are, 0-1.
    Both are already 0-100, so their gap is directly comparable."""
    breakdown = match.breakdown
    return min(1.0, abs(breakdown.skills - breakdown.semantic_fit) / 100)


def score_candidate(match: JobMatch) -> EnrichmentCandidate:
    boundary = _boundary_proximity(match.practical_fit)
    disagreement = _disagreement(match)
    # No recorded confidence means the pre-hybrid path produced it; treat that as
    # middling rather than as certainty.
    uncertainty = 1.0 - (match.confidence if match.confidence is not None else 0.5)
    rank = match.practical_fit / 100

    priority = (
        boundary * WEIGHT_BOUNDARY
        + disagreement * WEIGHT_DISAGREEMENT
        + uncertainty * WEIGHT_UNCERTAINTY
        + rank * WEIGHT_SCORE
    )
    contributions = {
        EnrichmentReason.DECISION_BOUNDARY: boundary * WEIGHT_BOUNDARY,
        EnrichmentReason.HIGH_DISAGREEMENT: disagreement * WEIGHT_DISAGREEMENT,
        EnrichmentReason.LOW_CONFIDENCE: uncertainty * WEIGHT_UNCERTAINTY,
        EnrichmentReason.TOP_RANKED: rank * WEIGHT_SCORE,
    }
    reason = max(contributions, key=lambda key: contributions[key])
    return EnrichmentCandidate(match=match, priority=round(priority, 4), reason=reason)


def is_eligible(match: JobMatch) -> bool:
    """Worth spending a call on at all. A rejected or ineligible job isn't going
    to be applied to whatever a model says about it, and one that already has a
    verdict would just be re-derived."""
    return (
        match.eligible
        and match.llm_assessment is None
        and match.recommendation is not Recommendation.SKIP
    )


def rank_for_enrichment(matches: list[JobMatch], limit: int) -> list[EnrichmentCandidate]:
    """The `limit` matches where an LLM opinion is most likely to change the
    user's decision, best first. Ties break by canonical job id so a rerun with
    the same inputs picks the same jobs."""
    candidates = [score_candidate(match) for match in matches if is_eligible(match)]
    candidates.sort(key=lambda entry: (-entry.priority, entry.match.canonical_job_id))
    return candidates[:limit]
