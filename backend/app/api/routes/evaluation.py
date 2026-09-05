"""The annotation surface — spec 20.1.

Annotation is the critical path, and a critical path with no interface is a
critical path nobody walks. Everything here exists so that judging a pair costs
a keystroke: 20.1 budgets a minute of careful reading per judgement, and any
part of that minute spent on the tool rather than the vacancy comes straight out
of how many pairs ever get judged.

The one thing these endpoints will not do is produce a label. Spec 20.1 rules
out generating judgements with a model and calling them ground truth, and this
is the layer where that shortcut would be easiest to take.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.db.session import get_session
from app.repositories.evaluation_repository import EvaluationRepository
from app.services.evaluation_service import EvaluationService

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


# 20.1's scale, spelled out where a person reads it rather than as bare integers.
LABEL_MEANINGS = {
    0: "irrelevant — I would not open this",
    1: "weak — adjacent, but not what I am looking for",
    2: "relevant — worth reading properly",
    3: "strong — I would apply to this",
}


class PairResponse(BaseModel):
    id: str
    canonical_job_id: str
    job_title: str
    job_company: str
    job_text: str
    # What the ranker thought, shown after a judgement rather than before it
    # would be better still; it is here because hiding it entirely makes the
    # queue impossible to debug. The UI keeps it out of the reading area.
    system_score: float | None
    tier: str


class JudgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal[0, 1, 2, 3] = Field(description="0 irrelevant, 1 weak, 2 relevant, 3 strong")


class ProgressResponse(BaseModel):
    counts: dict[str, int]
    label_distribution: dict[str, int]
    labels: dict[str, str]


class SampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: int = Field(default=300, ge=1, le=2000)
    tier: Literal["seed", "core", "full"] = "seed"


@router.get("/next", response_model=PairResponse | None)
async def next_pair(
    user_id: uuid.UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> PairResponse | None:
    """The next pair awaiting a judgement, highest-scoring first.

    Null when the queue is empty, which is a state to render rather than an
    error: an exhausted queue means either the set is fully judged or nobody has
    sampled one yet, and both are normal.
    """
    pairs = await EvaluationRepository(session).next_to_judge(limit=1)
    if not pairs:
        return None
    pair = pairs[0]
    return PairResponse(
        id=pair.id,
        canonical_job_id=pair.canonical_job_id,
        job_title=pair.job_title,
        job_company=pair.job_company,
        job_text=pair.job_text,
        system_score=pair.system_score,
        tier=pair.tier,
    )


@router.post("/{pair_id}/judge", response_model=ProgressResponse)
async def judge_pair(
    pair_id: uuid.UUID,
    payload: JudgeRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ProgressResponse:
    """Record one judgement and hand back the progress, so the UI needs one call."""
    repository = EvaluationRepository(session)
    if not await repository.judge(pair_id, payload.label, annotator=str(user_id)):
        raise HTTPException(status_code=404, detail="no such evaluation pair")
    return await _progress(repository)


@router.post("/{pair_id}/unjudge", response_model=ProgressResponse)
async def unjudge_pair(
    pair_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ProgressResponse:
    """Take a judgement back.

    A mis-clicked label that cannot be undone is a label somebody works around
    by leaving it wrong, and a wrong label is worse than a missing one — the
    metric reports it with full confidence.
    """
    repository = EvaluationRepository(session)
    if not await repository.clear_judgement(pair_id):
        raise HTTPException(status_code=404, detail="no such evaluation pair")
    return await _progress(repository)


@router.get("/progress", response_model=ProgressResponse)
async def progress(
    user_id: uuid.UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ProgressResponse:
    return await _progress(EvaluationRepository(session))


@router.post("/sample", response_model=dict)
async def sample(
    payload: SampleRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Add pairs to the queue, stratified across score bands and languages."""
    result = await EvaluationService(session).sample(size=payload.size, tier=payload.tier)
    if result is None:
        raise HTTPException(status_code=409, detail="no parsed CV to evaluate against")
    return {
        "added": result.added,
        "considered": result.considered,
        "skipped_existing": result.skipped_existing,
        "coverage": result.coverage,
    }


@router.get("/report", response_model=dict)
async def report(
    user_id: uuid.UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The 20.4 metrics over whatever has been judged so far.

    Deliberately available at any point rather than only once a tier is full: a
    number over forty judgements is a weak claim, and the report says how many
    it rests on, which is what makes it readable rather than misleading.
    """
    result = await EvaluationService(session).report()
    if result is None:
        raise HTTPException(status_code=409, detail="no parsed CV to evaluate against")
    return result.as_record()


async def _progress(repository: EvaluationRepository) -> ProgressResponse:
    return ProgressResponse(
        counts=await repository.progress(),
        label_distribution={
            str(label): count for label, count in (await repository.label_distribution()).items()
        },
        labels={str(value): meaning for value, meaning in LABEL_MEANINGS.items()},
    )
