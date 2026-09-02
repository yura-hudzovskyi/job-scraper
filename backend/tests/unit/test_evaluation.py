"""The metrics have to be right before any number they produce means anything —
especially the two that decide whether a change ships: macro F1 over an
unbalanced label set, and gap precision, where a false gap is the expensive kind.
"""

from pathlib import Path

from app.domain.matching.models import Recommendation
from app.evaluation.dataset import load_dataset
from app.evaluation.metrics import calibration_error, gap_report, recommendation_report

_EXAMPLE = Path(__file__).resolve().parents[2] / "evaluation" / "example_dataset.json"

APPLY, CONSIDER, SKIP = Recommendation.APPLY, Recommendation.CONSIDER, Recommendation.SKIP


def test_a_perfect_run_scores_one() -> None:
    report = recommendation_report([APPLY, CONSIDER, SKIP], [APPLY, CONSIDER, SKIP])

    assert report.macro_f1 == 1.0


def test_always_saying_skip_does_not_look_good() -> None:
    # The reason this is macro F1 and not accuracy: most jobs are a skip, so
    # accuracy would reward a pipeline that never recommends anything.
    expected = [SKIP, SKIP, SKIP, SKIP, APPLY, CONSIDER]
    predicted = [SKIP] * 6

    report = recommendation_report(expected, predicted)

    assert report.macro_f1 < 0.4
    assert report.per_class_f1["apply"] == 0.0


def test_a_class_nobody_labelled_is_not_averaged_in() -> None:
    # Otherwise the metric measures the dataset's coverage, not the pipeline.
    report = recommendation_report([APPLY, APPLY], [APPLY, APPLY])

    assert report.macro_f1 == 1.0


def test_a_false_gap_costs_precision() -> None:
    report = gap_report([["Go"]], [["Go", "Rust"]])

    assert report.false_gaps == 1
    assert report.precision == 0.5
    assert report.recall == 1.0


def test_a_missed_gap_costs_recall_only() -> None:
    report = gap_report([["Go", "Rust"]], [["Go"]])

    assert report.missed_gaps == 1
    assert report.recall == 0.5
    assert report.precision == 1.0


def test_gap_names_are_compared_through_the_ontology() -> None:
    # "Postgres" and "PostgreSQL" are one gap, not a false positive plus a miss.
    report = gap_report([["PostgreSQL"]], [["Postgres"]])

    assert report.precision == 1.0
    assert report.recall == 1.0


def test_no_gaps_on_either_side_is_not_a_failure() -> None:
    report = gap_report([[]], [[]])

    assert report.precision == 1.0
    assert report.recall == 1.0


def test_perfect_confidence_calibration_is_zero_error() -> None:
    assert calibration_error([1.0, 1.0], [True, True]) == 0.0
    assert calibration_error([0.0, 0.0], [False, False]) == 0.0


def test_confident_and_wrong_is_the_worst_calibration() -> None:
    assert calibration_error([0.95, 0.95], [False, False]) > 0.9


def test_the_example_dataset_parses_into_real_domain_objects() -> None:
    pairs = load_dataset(_EXAMPLE)

    assert [pair.id for pair in pairs] == [
        "obvious-match",
        "one-real-blocker",
        "keyword-heavy-mismatch",
    ]
    blocker = pairs[1]
    assert blocker.recommendation is CONSIDER
    assert blocker.missing_required == ["Go"]
    assert blocker.job.skills[0].name == "Go"
    assert blocker.profile.skills[0].name == "Python"
    # The keyword-trap pair exists to catch a pipeline that treats a mention as a
    # requirement.
    assert all(skill.requirement.value == "context" for skill in pairs[2].job.skills)
