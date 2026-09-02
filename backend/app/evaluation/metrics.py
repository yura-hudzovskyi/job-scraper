"""Scoring the scorer — see docs/ai-pipeline-v3.md (12).

Only the metrics that answer a question this project actually asks:

- **Macro F1 over recommendations**, not accuracy: the three classes are
  unbalanced (most jobs are a skip), and accuracy would reward a pipeline that
  says "skip" to everything.
- **Gap precision and recall**, kept separate, because their failures are not
  equally bad. A false gap tells someone they are unqualified when they aren't —
  it is the failure this whole design is arranged to avoid — while a missed gap
  merely leaves a surprise for the interview.
- **Calibration error**, because a confidence nobody has checked is decoration.
  Buckets rather than a fitted curve: with a few hundred labelled pairs, a curve
  would be fitting noise.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.matching.models import Recommendation
from app.domain.skills.normalizer import dedupe_key


@dataclass(frozen=True)
class ClassificationReport:
    macro_f1: float
    per_class_f1: dict[str, float]
    confusion: dict[tuple[str, str], int]  # (expected, predicted) -> count


@dataclass(frozen=True)
class GapReport:
    precision: float
    recall: float
    false_gaps: int
    missed_gaps: int


def recommendation_report(
    expected: Sequence[Recommendation], predicted: Sequence[Recommendation]
) -> ClassificationReport:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must line up one-to-one")

    labels = [Recommendation.APPLY, Recommendation.CONSIDER, Recommendation.SKIP]
    confusion: dict[tuple[str, str], int] = {}
    for want, got in zip(expected, predicted, strict=True):
        key = (want.value, got.value)
        confusion[key] = confusion.get(key, 0) + 1

    per_class: dict[str, float] = {}
    for label in labels:
        true_positive = sum(
            1 for want, got in zip(expected, predicted, strict=True) if want is label and got is label
        )
        predicted_positive = sum(1 for got in predicted if got is label)
        actual_positive = sum(1 for want in expected if want is label)
        precision = true_positive / predicted_positive if predicted_positive else 0.0
        recall = true_positive / actual_positive if actual_positive else 0.0
        per_class[label.value] = (
            0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        )

    # Classes absent from the labels are excluded rather than counted as zero:
    # averaging in an F1 for a class nobody labelled measures the dataset, not
    # the pipeline.
    present = [label.value for label in labels if any(want is label for want in expected)]
    macro = sum(per_class[label] for label in present) / len(present) if present else 0.0
    return ClassificationReport(
        macro_f1=round(macro, 4), per_class_f1=per_class, confusion=confusion
    )


def gap_report(
    expected: Sequence[Sequence[str]], predicted: Sequence[Sequence[str]]
) -> GapReport:
    """Names are compared through the ontology, so "Postgres" and "PostgreSQL"
    are one gap rather than a false positive and a miss."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must line up one-to-one")

    true_positive = false_positive = false_negative = 0
    for want, got in zip(expected, predicted, strict=True):
        want_keys = {dedupe_key(name) for name in want}
        got_keys = {dedupe_key(name) for name in got}
        true_positive += len(want_keys & got_keys)
        false_positive += len(got_keys - want_keys)
        false_negative += len(want_keys - got_keys)

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    return GapReport(
        precision=round(precision, 4),
        recall=round(recall, 4),
        false_gaps=false_positive,
        missed_gaps=false_negative,
    )


def calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], buckets: int = 5
) -> float:
    """Expected calibration error: how far "80% confident" is from being right
    80% of the time. 0 is perfect; anything under ~0.1 is respectable for a
    hand-shaped mapping."""
    if len(confidences) != len(correct):
        raise ValueError("confidences and correctness must line up one-to-one")
    if not confidences:
        return 0.0

    total = len(confidences)
    error = 0.0
    for index in range(buckets):
        low, high = index / buckets, (index + 1) / buckets
        members = [
            (confidence, is_correct)
            for confidence, is_correct in zip(confidences, correct, strict=True)
            if (low < confidence <= high) or (index == 0 and confidence == 0.0)
        ]
        if not members:
            continue
        mean_confidence = sum(confidence for confidence, _ in members) / len(members)
        accuracy = sum(1 for _, is_correct in members if is_correct) / len(members)
        error += len(members) / total * abs(mean_confidence - accuracy)
    return round(error, 4)
