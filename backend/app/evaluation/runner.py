"""Runs the deterministic half of the pipeline over a labelled set and reports how
it did — see docs/ai-pipeline-v3.md (12).

Deliberately offline and LLM-free. What it measures is the part that runs for
every job regardless of quota: requirement matching, the hybrid score, the gaps
and the confidence. The LLM layer is evaluated by comparing two runs of *this*
(enrichment on and off) once there is a labelled set worth the calls.

Usage:

    python -m app.evaluation.runner path/to/dataset.json

It prints a report and exits non-zero on nothing — this is a measuring tool, not
a gate. Turning it into a gate needs numbers to gate on, which is what the first
real dataset produces.
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass

from app.config.settings import get_settings
from app.domain.candidates.models import UserPreference
from app.domain.categories import candidate_categories, decide
from app.domain.matching.hybrid import HybridMatchEngine, recommend
from app.domain.matching.models import Recommendation
from app.domain.matching.scoring import DeterministicScorer, SemanticScorer
from app.domain.matching.skill_matching import SkillMatcher
from app.evaluation.dataset import LabeledPair, load_dataset
from app.evaluation.metrics import calibration_error, gap_report, recommendation_report
from app.integrations.ai.embeddings.factory import build_embedding_provider


@dataclass(frozen=True)
class PairOutcome:
    id: str
    expected: str
    predicted: str
    score: float
    confidence: float
    expected_gaps: list[str]
    predicted_gaps: list[str]


async def evaluate(pairs: list[LabeledPair]) -> list[PairOutcome]:
    settings = get_settings()
    embedding_provider = build_embedding_provider(settings)
    if embedding_provider is None:
        raise RuntimeError("no embedding provider configured — evaluation needs one")

    matcher = SkillMatcher(embedding_provider)
    # Real similarity, not a placeholder: role/domain fit is the signal that
    # catches a vacancy from another profession, and pinning it at a neutral
    # constant would hide exactly the failure this set exists to find.
    semantic = SemanticScorer(embedding_provider)
    scorer = DeterministicScorer()
    engine = HybridMatchEngine()

    outcomes = []
    for pair in pairs:
        preferences = UserPreference(user_id=pair.profile.user_id, desired_salary_usd=None)
        skills = await matcher.assess(
            job_skills=pair.job.skills,
            candidate_skills=[skill.name for skill in pair.profile.skills],
            preferred_stack=[],
            acceptable_stack=[],
        )
        _, salary, location = scorer.score(pair.job, pair.profile, preferences)
        semantic_fit = await semantic.similarity(pair.job, pair.profile) * 100
        result = engine.evaluate(
            job=pair.job,
            profile=pair.profile,
            preferences=preferences,
            skills=skills,
            semantic_fit=semantic_fit,
            role_fit=semantic_fit,
            salary_score=salary,
            location_score=location,
            category=decide(
                pair.job.category,
                pair.job.category_confidence,
                candidate_categories(pair.profile.roles),
            ),
        )
        outcomes.append(
            PairOutcome(
                id=pair.id,
                expected=pair.recommendation.value,
                predicted=recommend(result.score, result.domain_mismatch).value,
                score=result.score,
                confidence=result.confidence,
                expected_gaps=pair.missing_required,
                # The domain-mismatch marker is a verdict, not a missing skill —
                # counting it as one would make gap precision measure the gate.
                predicted_gaps=[
                    gap.label
                    for gap in result.gaps
                    if gap.critical and gap.label != "role/domain mismatch"
                ],
            )
        )
    return outcomes


def report(outcomes: list[PairOutcome]) -> dict[str, object]:
    classification = recommendation_report(
        [Recommendation(outcome.expected) for outcome in outcomes],
        [Recommendation(outcome.predicted) for outcome in outcomes],
    )
    gaps = gap_report(
        [outcome.expected_gaps for outcome in outcomes],
        [outcome.predicted_gaps for outcome in outcomes],
    )
    calibration = calibration_error(
        [outcome.confidence for outcome in outcomes],
        [outcome.expected == outcome.predicted for outcome in outcomes],
    )
    return {
        "pairs": len(outcomes),
        "recommendation": asdict(classification) | {"confusion": None},
        "gaps": asdict(gaps),
        "calibration_error": calibration,
        "disagreements": [
            asdict(outcome) for outcome in outcomes if outcome.expected != outcome.predicted
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="path to a labelled dataset JSON file")
    arguments = parser.parse_args()

    pairs = load_dataset(arguments.dataset)
    outcomes = asyncio.run(evaluate(pairs))
    print(json.dumps(report(outcomes), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
