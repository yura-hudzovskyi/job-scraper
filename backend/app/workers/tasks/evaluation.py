"""Building and scoring the evaluation set, as background tasks.

Sampling reads every match the ranker has produced and writes a few hundred
rows; scoring re-ranks the whole set. Both are seconds of work, but neither
belongs in a request: the point of an evaluation set is that it is rebuilt and
re-scored on a schedule of its own, around model changes, not around page loads.
"""

import asyncio
import logging
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import text

from app.db.session import session_scope
from app.services.evaluation_service import EvaluationService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _sample(user_id: uuid.UUID, size: int, tier: str) -> dict[str, Any]:
    async with session_scope() as session:
        result = await EvaluationService(session).sample(user_id, size=size, tier=tier)
    if result is None:
        return {"status": "no candidate", "added": 0}
    return {"status": "ok", **asdict(result)}


async def _report(user_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        report = await EvaluationService(session).report(user_id)
    if report is None:
        return {"status": "no candidate"}
    return {"status": "ok", **report.as_record()}


@celery_app.task(name="evaluation.sample_pairs")
def sample_pairs(user_id: str, size: int = 300, tier: str = "seed") -> dict[str, Any]:
    """Queue pairs for judging, stratified across score bands and languages.

    Re-runnable: pairs already in the set are skipped without touching their
    labels, so running it after a scrape grows the set rather than resetting it.

    `user_id` is required rather than defaulted. The version that guessed picked
    whichever CV was uploaded last across the whole install, and quietly built a
    software engineer's evaluation set out of a 3D artist's rankings.
    """
    return asyncio.run(_sample(uuid.UUID(user_id), size, tier))


@celery_app.task(name="evaluation.report")
def report(user_id: str) -> dict[str, Any]:
    """Score the live ranking against the judgements that exist (spec 20.4)."""
    return asyncio.run(_report(uuid.UUID(user_id)))


# --- repairing the set built against the wrong CV ----------------------------

# Move a candidate's pairs to the annotator who actually judged them. Only rows
# the target does not already have, so a term judged for both candidates keeps
# the target's own answer rather than being overwritten by the other one.
_REPOINT = """
    UPDATE evaluation_pairs p
    SET candidate_revision_id = :target
    WHERE p.candidate_revision_id = :source
      AND p.annotator = :annotator
      AND NOT EXISTS (
          SELECT 1 FROM evaluation_pairs q
          WHERE q.candidate_revision_id = :target
            AND q.canonical_job_id = p.canonical_job_id
      )
    RETURNING p.id
"""


async def _repoint(source: uuid.UUID, target: uuid.UUID, annotator: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        moved = (
            (
                await session.execute(
                    text(_REPOINT),
                    {"source": source, "target": target, "annotator": str(annotator)},
                )
            )
            .scalars()
            .all()
        )
    return {"status": "done", "moved": len(moved)}


@celery_app.task(name="evaluation.repoint_pairs")
def repoint_pairs(source: str, target: str, annotator: str) -> dict[str, Any]:
    """Reattach judgements to the candidate they were really about.

    Written for one incident and kept because the incident is the general case.
    A scoping bug served one person's queue from another person's rankings, so
    600 pairs were judged by a software engineer against a 3D artist's CV.

    The judgements themselves are unaffected by that: "would I want this
    vacancy" is an answer about the annotator and the vacancy, and whose CV the
    ranker happened to be using does not enter into it. What was wrong is one
    column, and this moves it.

    What it will not do is move a pair the target already has. That row carries
    the target's own answer, and a repair that overwrites real judgements with
    other real judgements is worse than the state it is fixing.
    """
    return asyncio.run(_repoint(uuid.UUID(source), uuid.UUID(target), uuid.UUID(annotator)))
