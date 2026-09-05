"""Building and scoring the evaluation set, as background tasks.

Sampling reads every match the ranker has produced and writes a few hundred
rows; scoring re-ranks the whole set. Both are seconds of work, but neither
belongs in a request: the point of an evaluation set is that it is rebuilt and
re-scored on a schedule of its own, around model changes, not around page loads.
"""

import asyncio
import logging
from dataclasses import asdict
from typing import Any

from app.db.session import session_scope
from app.services.evaluation_service import EvaluationService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _sample(size: int, tier: str) -> dict[str, Any]:
    async with session_scope() as session:
        result = await EvaluationService(session).sample(size=size, tier=tier)
    if result is None:
        return {"status": "no candidate", "added": 0}
    return {"status": "ok", **asdict(result)}


async def _report() -> dict[str, Any]:
    async with session_scope() as session:
        report = await EvaluationService(session).report()
    if report is None:
        return {"status": "no candidate"}
    return {"status": "ok", **report.as_record()}


@celery_app.task(name="evaluation.sample_pairs")
def sample_pairs(size: int = 300, tier: str = "seed") -> dict[str, Any]:
    """Queue pairs for judging, stratified across score bands and languages.

    Re-runnable: pairs already in the set are skipped without touching their
    labels, so running it after a scrape grows the set rather than resetting it.
    """
    return asyncio.run(_sample(size, tier))


@celery_app.task(name="evaluation.report")
def report() -> dict[str, Any]:
    """Score the live ranking against the judgements that exist (spec 20.4)."""
    return asyncio.run(_report())
