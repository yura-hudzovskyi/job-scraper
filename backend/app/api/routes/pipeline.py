"""Operator controls for the AI pipeline — see docs/ai-pipeline-v3.md.

The pipeline is four stages that have to happen in order, and each one is
minutes-to-hours of background work over the whole corpus. Without a place to see
and start them, "why does this job say no requirements were extracted" has no
answer a user can act on.

Every trigger here is idempotent and refuses to run twice: `PipelineRunState`
holds a server-side lock, so a second tab, a stale page or a curl call can't
start a parallel pass. `GET /api/ai/pipeline` reports what is running and what is
missing, which is what lets the UI disable a button *and say why* rather than
failing silently when it is pressed.

Order matters, and the status endpoint exposes exactly what each step needs:

1. **Extraction + scoring** — reads every posting for its requirements and scores
   it. Everything downstream is meaningless without requirements.
2. **Embeddings** — builds the section vectors, per lane. A lane only serves
   queries once it covers the corpus.
3. **Retrieval + rerank** — ranks the whole corpus for one candidate and leaves a
   relevance the next scoring pass folds into the score.
"""

import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import (
    get_candidate_repository,
    get_current_user_id,
    get_embedding_repository,
    get_job_repository,
    get_match_repository,
)
from app.config.settings import get_settings
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.embedding_repository import JOB, PROFILE, EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.services.embedding_indexing_service import READY
from app.services.pipeline_state import PipelineRunState
from app.workers.tasks.backfill import rescore_all_jobs
from app.workers.tasks.embed import rebuild_all_embeddings
from app.workers.tasks.retrieve import rank_all

router = APIRouter(prefix="/api/ai/pipeline", tags=["ai"])


def _run_state() -> PipelineRunState:
    return PipelineRunState(redis.from_url(get_settings().redis_url))


class LaneCoverage(BaseModel):
    id: str
    role: str
    state: str
    jobs_covered: int


class PipelineStatus(BaseModel):
    """Everything the UI needs to decide which buttons make sense right now."""

    jobs_total: int
    matches_total: int
    matches_hybrid_scored: int
    matches_enriched: int
    matches_with_relevance: int
    # Requirements are the input everything else depends on; this is how many
    # matches were scored without any.
    profile_indexed: bool
    has_profile: bool
    lanes: list[LaneCoverage]
    embeddings_ready: bool
    running: dict[str, bool]


class TriggerResponse(BaseModel):
    status: str
    detail: str


@router.get("", response_model=PipelineStatus)
async def get_pipeline_status(
    user_id: uuid.UUID = Depends(get_current_user_id),
    job_repository: JobRepository = Depends(get_job_repository),
    match_repository: MatchRepository = Depends(get_match_repository),
    embedding_repository: EmbeddingRepository = Depends(get_embedding_repository),
    candidate_repository: CandidateRepository = Depends(get_candidate_repository),
) -> PipelineStatus:
    profile = await candidate_repository.get_latest_candidate_profile(user_id)
    lanes = await embedding_repository.list_lanes()
    counts = await match_repository.count_for_user(user_id)

    return PipelineStatus(
        jobs_total=await job_repository.count_canonical_jobs(),
        matches_total=counts["total"],
        matches_hybrid_scored=counts["hybrid_scored"],
        matches_enriched=counts["enriched"],
        matches_with_relevance=counts["with_relevance"],
        has_profile=profile is not None,
        profile_indexed=(
            await embedding_repository.has_vectors(PROFILE, uuid.UUID(profile.id))
            if profile
            else False
        ),
        lanes=[
            LaneCoverage(
                id=lane.id,
                role=lane.role,
                state=lane.state,
                jobs_covered=await embedding_repository.documents_with_vectors(lane.id, JOB),
            )
            for lane in lanes
        ],
        embeddings_ready=any(lane.state == READY for lane in lanes),
        running=await _run_state().running(),
    )


@router.post("/scoring/run", response_model=TriggerResponse)
async def run_scoring(user_id: uuid.UUID = Depends(get_current_user_id)) -> TriggerResponse:
    """Step 1: re-read every posting's requirements and rescore every vacancy.

    This is the one to run first and after any model change — everything
    downstream compares against the requirements it extracts, and a job with none
    is scored on text similarity alone.
    """
    rescore_all_jobs.delay(str(user_id))
    return TriggerResponse(
        status="queued",
        detail="Re-extracting requirements and rescoring every vacancy in the background.",
    )


@router.post("/embeddings/rebuild", response_model=TriggerResponse)
async def rebuild_embeddings(user_id: uuid.UUID = Depends(get_current_user_id)) -> TriggerResponse:
    """Step 2: delete every stored vector and rebuild from scratch.

    Deliberately destructive: a lane half-filled by a previous model, or marked
    ready when it only covers last month's corpus, is harder to trust than an
    empty one, and everything here is recomputable from the postings.
    """
    rebuild_all_embeddings.delay([str(user_id)])
    return TriggerResponse(
        status="queued",
        detail="Clearing every vector, then re-indexing every vacancy and your CV.",
    )


@router.post("/retrieval/run", response_model=TriggerResponse)
async def run_retrieval(user_id: uuid.UUID = Depends(get_current_user_id)) -> TriggerResponse:
    """Step 3: rank the whole corpus for this candidate and rerank the shortlist.

    Needs a lane that covers the corpus; without one it does nothing rather than
    ranking whatever happens to be indexed.
    """
    rank_all.delay(str(user_id))
    return TriggerResponse(
        status="queued",
        detail="Ranking every vacancy against your CV, then reranking the shortlist.",
    )
