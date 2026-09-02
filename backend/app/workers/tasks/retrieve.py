"""Retrieval and reranking as an explicit pass over one user's corpus — see
docs/ai-pipeline-v3.md (C5, D).

Scoring answers "how well does this vacancy fit" one job at a time. This answers
the question scoring can't: *of everything in the database, which vacancies are
worth looking at at all*, by comparing section vectors inside one ready lane and
then reranking the shortlist with a model that reads both documents together.

What it leaves behind is a calibrated relevance on each match, which is the
missing input the hybrid engine has been substituting semantic similarity for.
The next scoring pass picks it up, so the order this produces survives into the
score rather than living in a separate list nobody reads.

Nothing here invents capacity: if no lane covers the corpus, it says so and does
nothing, because retrieving from a half-built index returns a smaller world
without announcing it.
"""

import asyncio
import uuid

import redis.asyncio as redis

from app.config.runtime_settings import get_effective_settings
from app.config.settings import get_settings
from app.db.session import session_scope
from app.domain.matching.documents import job_sections, profile_sections
from app.domain.matching.rerank import RerankService
from app.domain.matching.retrieval import RetrievalService
from app.integrations.ai.embeddings.lanes import lanes_for
from app.integrations.ai.rerank.factory import rerank_engines
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.services.pipeline_state import PipelineRunState, PipelineStage
from app.workers.celery_app import celery_app

# How many vacancies retrieval hands to the reranker. Reranking is the expensive
# half, and past a few hundred the ordering below the fold stops mattering.
RETRIEVE_LIMIT = 150
RERANK_LIMIT = 100


def _document(job) -> str:
    """The same compact section text the vectors were built from — a reranker
    reading a different rendering than retrieval used would be ranking something
    else."""
    return "\n".join(job_sections(job).values())


async def _run(user_id: str) -> dict[str, int]:
    settings = await get_effective_settings(get_settings())
    state = PipelineRunState(redis.from_url(settings.redis_url))
    if not await state.start(PipelineStage.RETRIEVAL):
        return {"started": 0}

    try:
        async with session_scope() as session:
            candidate_repository = CandidateRepository(session)
            job_repository = JobRepository(session)
            match_repository = MatchRepository(session)
            embedding_repository = EmbeddingRepository(session)

            profile = await candidate_repository.get_latest_candidate_profile(uuid.UUID(user_id))
            if profile is None:
                return {"started": 1, "retrieved": 0, "reranked": 0}
            preferences = await candidate_repository.get_preferences(uuid.UUID(user_id))

            retrieval = RetrievalService(
                embedding_repository, job_repository, lanes_for(settings)
            )
            result = await retrieval.retrieve(profile, preferences, limit=RETRIEVE_LIMIT)
            if not result.usable:
                # No lane covers the corpus yet — the honest answer is "nothing",
                # not a ranking over whichever jobs happen to be indexed.
                return {"started": 1, "retrieved": 0, "reranked": 0}

            documents: dict[uuid.UUID, str] = {}
            for retrieved in result.jobs[:RERANK_LIMIT]:
                job = await job_repository.get_normalized_job_for_canonical(
                    retrieved.canonical_job_id
                )
                if job is not None:
                    documents[retrieved.canonical_job_id] = _document(job)

            candidate_document = "\n".join(profile_sections(profile, preferences).values())
            reranked = await RerankService(rerank_engines(settings)).rerank(
                candidate_document, documents
            )

            # Retrieval's own score stands in wherever the reranker didn't reach,
            # so every retrieved vacancy carries a comparable relevance.
            relevance = {job.canonical_job_id: job.score for job in result.jobs}
            relevance.update({job.canonical_job_id: job.relevance for job in reranked.jobs})

            updated = await match_repository.set_relevance(
                uuid.UUID(user_id), relevance, reranked.model_id, result.lane_id
            )
        return {
            "started": 1,
            "retrieved": len(result.jobs),
            "reranked": len(reranked.jobs),
            "updated": updated,
        }
    finally:
        await state.finish(PipelineStage.RETRIEVAL)


@celery_app.task(name="retrieve.rank_all")
def rank_all(user_id: str) -> dict[str, int]:
    return asyncio.run(_run(user_id))
