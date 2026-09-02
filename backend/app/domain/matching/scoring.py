"""Turning two model outputs into one number — the entire scoring rule.

`similarity` is the cosine between the CV's vector and the vacancy's, 0-1.
`relevance` is what the reranker said about the same pair, also 0-1, and only
exists for the jobs the reranker actually read.

The blend is a straight weighted average, on purpose. There is no calibration
layer, no confidence model and no hand-tuned component weights, because there is
nothing here to calibrate against: two honest numbers and the weight between them
is something a user can read off the screen and reason about, which a fitted
curve would not be.
"""

from app.domain.matching.models import Recommendation


def combine(similarity: float, relevance: float | None, rerank_weight: float) -> float:
    """Final 0-100 score. A job the reranker never saw scores on similarity
    alone rather than being penalised for it."""
    similarity = _clamp(similarity)
    if relevance is None:
        return round(similarity * 100, 1)
    weight = _clamp(rerank_weight)
    blended = similarity * (1 - weight) + _clamp(relevance) * weight
    return round(blended * 100, 1)


def recommend(score: float, apply_threshold: float, consider_threshold: float) -> Recommendation:
    if score >= apply_threshold:
        return Recommendation.APPLY
    if score >= consider_threshold:
        return Recommendation.CONSIDER
    return Recommendation.SKIP


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
