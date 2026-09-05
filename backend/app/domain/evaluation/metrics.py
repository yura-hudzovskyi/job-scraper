"""Ranking metrics over judged pairs — spec 20.4.

Pure arithmetic over (label, rank), deliberately knowing nothing about how the
ranking was produced. That is what lets the same functions score today's
retrieval-only pipeline and an extraction-aware one, which is the comparison
3.5.2 condition 3 gates on: extraction may not touch a score until a set like
this says it beats the baseline.

Two things this module refuses to do, both because the answer would look fine.

It does not treat an unjudged pair as irrelevant. A `None` label means nobody
looked, and scoring it as 0 would quietly reward a ranker for surfacing things
the annotator never saw. Unjudged pairs are excluded and counted, so a metric
computed over a thin set says so.

It does not report a metric it cannot compute. No judged relevant pair means
Recall has no denominator, and returning 0.0 there would read as a failing
system rather than an empty question.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

# 20.1's scale: 0 irrelevant, 1 weak, 2 relevant, 3 strong. "Relevant" for the
# binary metrics (Recall, MRR, Precision) starts at 2 — a `weak` match is not
# one a person would want surfaced as an answer.
RELEVANT_FROM = 2


@dataclass(frozen=True)
class Judged:
    """One pair as the metrics see it: where the system ranked it, what a person said."""

    rank: int  # 1-based position in the system's ranking
    label: int | None


@dataclass(frozen=True)
class RankingMetrics:
    judged: int
    unjudged: int
    relevant: int
    recall_at: dict[int, float | None]
    ndcg_at: dict[int, float | None]
    precision_at: dict[int, float | None]
    mrr_at_10: float | None

    def as_record(self) -> dict[str, object]:
        return {
            "judged": self.judged,
            "unjudged": self.unjudged,
            "relevant": self.relevant,
            "recall_at": {str(k): v for k, v in self.recall_at.items()},
            "ndcg_at": {str(k): v for k, v in self.ndcg_at.items()},
            "precision_at": {str(k): v for k, v in self.precision_at.items()},
            "mrr_at_10": self.mrr_at_10,
        }


def _gain(label: int) -> float:
    """Graded gain. 2**label - 1 spreads 0/1/2/3 into 0/1/3/7.

    The standard choice, and the one that matters here: it says a `strong` match
    at rank 1 is worth more than two `relevant` ones, which is the judgement a
    person makes when they open the first result and stop.
    """
    return float(2**label - 1)


def _labelled(judged: Sequence[Judged]) -> list[tuple[int, int]]:
    """(rank, label) for pairs somebody actually judged.

    Narrowing happens once, here, rather than with a `is not None` guard inside
    every comprehension — those read as defensive noise and one of them being
    forgotten is exactly how an unjudged pair ends up scored as irrelevant.
    """
    return [(item.rank, item.label) for item in judged if item.label is not None]


def recall_at(judged: Sequence[Judged], k: int) -> float | None:
    """Share of relevant pairs the ranking put in its top k.

    The retrieval gate (20.4): a reranker cannot recover a relevant job that
    retrieval never returned, so this is measured before reranking and is the
    number that decides whether reranker work is worth doing at all.
    """
    relevant = [rank for rank, label in _labelled(judged) if label >= RELEVANT_FROM]
    if not relevant:
        return None
    return sum(1 for rank in relevant if rank <= k) / len(relevant)


def precision_at(judged: Sequence[Judged], k: int) -> float | None:
    """Share of the top k that is relevant, over the judged ones only.

    Positions nobody judged are left out of both halves of the fraction rather
    than counted as failures — the ranker is not penalised for showing something
    the annotator has not looked at yet.
    """
    top = [label for rank, label in _labelled(judged) if rank <= k]
    if not top:
        return None
    return sum(1 for label in top if label >= RELEVANT_FROM) / len(top)


def ndcg_at(judged: Sequence[Judged], k: int) -> float | None:
    """Discounted gain against the best ordering the same labels allow.

    Normalised, so a candidate with three strong matches and one with none are
    on the same scale — without that, an average across candidates would mostly
    measure how many good vacancies each happened to have.
    """
    labelled = _labelled(judged)
    if not labelled:
        return None

    actual = sum(_gain(label) / math.log2(rank + 1) for rank, label in labelled if rank <= k)
    ideal_labels = sorted((label for _, label in labelled), reverse=True)
    ideal = sum(
        _gain(label) / math.log2(position + 2) for position, label in enumerate(ideal_labels[:k])
    )
    if ideal == 0:
        # Every judged pair is irrelevant. The ranking cannot be wrong about an
        # ordering with nothing to order, and 0.0 would read as a failure.
        return None
    return actual / ideal


def mrr_at(judged: Sequence[Judged], k: int = 10) -> float | None:
    """1/rank of the first relevant pair — how far down the first useful answer is."""
    labelled = _labelled(judged)
    if not labelled:
        return None
    ranks = [rank for rank, label in labelled if label >= RELEVANT_FROM and rank <= k]
    if not ranks:
        return 0.0
    return 1.0 / min(ranks)


def evaluate(
    judged: Sequence[Judged], ks: Sequence[int] = (5, 10, 20, 50, 100, 200)
) -> RankingMetrics:
    """Every 20.4 metric over one candidate's ranking."""
    labelled = _labelled(judged)
    return RankingMetrics(
        judged=len(labelled),
        unjudged=len(judged) - len(labelled),
        relevant=sum(1 for _, label in labelled if label >= RELEVANT_FROM),
        recall_at={k: recall_at(judged, k) for k in ks},
        ndcg_at={k: ndcg_at(judged, k) for k in ks if k <= 20},
        precision_at={k: precision_at(judged, k) for k in (10,)},
        mrr_at_10=mrr_at(judged, 10),
    )
