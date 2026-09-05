"""The arithmetic that will decide whether extraction is allowed into scoring.

Spec 3.5.2 condition 3 makes an evaluation set the gate on extraction affecting
any score, so these numbers carry a decision. Most of what is tested here is
what the metrics refuse to report, because a metric that returns 0.0 when it
means "no data" turns an empty question into a failing grade.
"""

from app.domain.evaluation.metrics import (
    Judged,
    evaluate,
    mrr_at,
    ndcg_at,
    precision_at,
    recall_at,
)


def _ranking(*labels: int | None) -> list[Judged]:
    """Labels in rank order, starting at rank 1."""
    return [Judged(rank=index + 1, label=label) for index, label in enumerate(labels)]


# --- recall ------------------------------------------------------------------


def test_recall_counts_relevant_pairs_inside_k() -> None:
    ranking = _ranking(3, 0, 2, 0, 0, 2)

    assert recall_at(ranking, 3) == 2 / 3
    assert recall_at(ranking, 10) == 1.0


def test_a_weak_match_does_not_count_as_relevant() -> None:
    """1 is `weak` on 20.1's scale — not something a person wants surfaced as an
    answer, so counting it would flatter every metric here."""
    assert recall_at(_ranking(1, 1, 1), 10) is None


def test_recall_with_nothing_relevant_is_unanswerable_not_zero() -> None:
    """No denominator. 0.0 would read as a failing ranker rather than an empty
    question, and the difference matters when the number gates a release."""
    assert recall_at(_ranking(0, 0, 0), 10) is None
    assert recall_at([], 10) is None


# --- nDCG --------------------------------------------------------------------


def test_the_ideal_ordering_scores_one() -> None:
    assert ndcg_at(_ranking(3, 2, 2, 0), 4) == 1.0


def test_a_reversed_ordering_scores_below_one() -> None:
    good = ndcg_at(_ranking(3, 2, 0, 0), 4)
    bad = ndcg_at(_ranking(0, 0, 2, 3), 4)

    assert good is not None and bad is not None
    assert good == 1.0
    assert bad < 0.6


def test_burying_a_strong_match_costs_more_than_linear_gain_would() -> None:
    """Graded gain, 2**label - 1, spreads 0/1/2/3 into 0/1/3/7.

    Only visible when one ranking holds different grades — nDCG normalises, so
    a single misplaced label scores the same whatever its grade. Here `strong`
    and `relevant` swap places: graded gain puts that at 0.83, linear gain would
    call it 0.91. The stricter number is the point, because it matches what a
    person does with the first result.
    """
    reversed_pair = ndcg_at([Judged(1, 2), Judged(2, 3)], 2)

    assert reversed_pair is not None
    assert reversed_pair < 0.85
    assert ndcg_at([Judged(1, 3), Judged(2, 2)], 2) == 1.0


def test_ndcg_over_only_irrelevant_labels_is_unanswerable() -> None:
    """The ranking cannot be wrong about an ordering with nothing to order."""
    assert ndcg_at(_ranking(0, 0, 0), 10) is None


# --- precision and MRR -------------------------------------------------------


def test_precision_ignores_positions_nobody_judged() -> None:
    """The ranker is not penalised for surfacing something the annotator has not
    looked at — that would reward a set for being incomplete."""
    ranking = [Judged(1, 2), Judged(2, None), Judged(3, 0)]

    assert precision_at(ranking, 10) == 0.5


def test_mrr_is_the_reciprocal_of_the_first_relevant_rank() -> None:
    assert mrr_at(_ranking(0, 0, 2, 3), 10) == 1 / 3


def test_mrr_is_zero_when_nothing_relevant_is_in_the_window() -> None:
    """Genuinely zero, not unanswerable: there were relevant pairs and the
    ranking put none of them in the top ten."""
    ranking = [Judged(1, 0), Judged(2, 0), Judged(50, 3)]

    assert mrr_at(ranking, 10) == 0.0


# --- unjudged pairs ----------------------------------------------------------


def test_an_unjudged_pair_is_never_treated_as_irrelevant() -> None:
    """A None label means nobody looked. Scoring it 0 would quietly reward a
    ranker for surfacing things the annotator never saw."""
    with_unjudged = evaluate([Judged(1, 3), Judged(2, None), Judged(3, None)])

    assert with_unjudged.judged == 1
    assert with_unjudged.unjudged == 2
    assert with_unjudged.ndcg_at[5] == 1.0


def test_the_report_says_how_thin_the_set_is() -> None:
    """ "nDCG 0.91" over four judged pairs is a different claim from the same
    number over three thousand, and the report has to carry both."""
    metrics = evaluate(_ranking(3, 2, 0, None, None, None))

    assert (metrics.judged, metrics.unjudged, metrics.relevant) == (3, 3, 2)


def test_an_empty_set_reports_nothing_rather_than_zeros() -> None:
    metrics = evaluate([])

    assert metrics.judged == 0
    assert metrics.mrr_at_10 is None
    assert all(value is None for value in metrics.recall_at.values())
    assert all(value is None for value in metrics.ndcg_at.values())
