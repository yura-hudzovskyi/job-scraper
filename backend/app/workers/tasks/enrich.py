"""Spending the day's LLM budget on the matches where it changes something — see
docs/ai-pipeline-v3.md (F3, F4).

Under MATCHING_PIPELINE_V3 the scoring path no longer calls an LLM per match.
Instead every match is scored by the hybrid engine, and this ranks them by value
of information (app/domain/matching/scheduling.py) and reviews the top of that
list. The difference in practice: the job sitting exactly on the apply/consider
line gets analysed even though it was scraped last.

Running out of capacity is a normal outcome, not a failure — the hybrid results
stand on their own, and the task comes back when the provider does rather than
leaving a half-enriched batch behind.
"""

import asyncio
import logging
import uuid
from datetime import timedelta

from app.config.runtime_settings import get_effective_settings
from app.config.settings import get_settings
from app.db.session import session_scope
from app.domain.matching.enrichment import LlmMatchEnricher, apply_enrichment
from app.domain.matching.hybrid import MatchDimensions
from app.domain.matching.models import JobMatch
from app.domain.matching.scheduling import rank_for_enrichment
from app.integrations.ai.llm.factory import build_llm_router
from app.integrations.ai.routing.router import Capability, NoCapacity
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.job_repository import JobRepository
from app.repositories.match_repository import MatchRepository
from app.workers.celery_app import celery_app
from app.workers.pacing import retry_countdown

logger = logging.getLogger(__name__)

# How many matches one pass will review. The capability budget is the real limit;
# this keeps a single run bounded so a backlog can't monopolise a worker.
DEFAULT_BATCH = 20
_MAX_CAPACITY_RETRIES = 2


def _dimensions(match: JobMatch) -> MatchDimensions:
    """The breakdown as the enricher's dimensions. Stored matches keep the
    breakdown field names, so this is the one place that translates."""
    breakdown = match.breakdown
    return MatchDimensions(
        required_skills=breakdown.skills,
        relevant_experience=breakdown.experience,
        seniority=breakdown.role,
        role_domain_fit=breakdown.semantic_fit,
        responsibilities=breakdown.semantic_fit,
        preferences=breakdown.preferences,
    )


async def _enrich_matches(user_id: str, matches: list[JobMatch], limit: int) -> timedelta | None:
    """Returns how long to wait when capacity ran out mid-batch, else None."""
    settings = await get_effective_settings(get_settings())
    provider = build_llm_router(Capability.MATCH_ENRICHMENT, settings)
    if provider is None:
        return None

    enricher = LlmMatchEnricher(provider)
    async with session_scope() as session:
        job_repository = JobRepository(session)
        match_repository = MatchRepository(session)
        profile = await CandidateRepository(session).get_latest_candidate_profile(
            uuid.UUID(user_id)
        )
        if profile is None:
            return None

        for candidate in rank_for_enrichment(matches, limit):
            match = candidate.match
            job = await job_repository.get_normalized_job_for_canonical(
                uuid.UUID(match.canonical_job_id)
            )
            if job is None:
                continue

            try:
                result = await enricher.enrich(
                    job=job,
                    profile=profile,
                    dimensions=_dimensions(match),
                    score=match.practical_fit,
                    confidence=match.confidence or 0.5,
                    recommendation=match.recommendation or _band_placeholder(match),
                    gaps=[gap.label for gap in match.gaps],
                    risks=list(match.risks),
                )
            except NoCapacity as exc:
                # Everything reviewed so far is saved; the rest keeps its hybrid
                # result until the provider reopens.
                return exc.retry_after or timedelta(minutes=30)
            except Exception:
                logger.warning(
                    "enrichment failed for match %s — leaving its hybrid result in place",
                    match.id,
                    exc_info=True,
                )
                continue

            await match_repository.upsert(apply_enrichment(match, result))
    return None


def _band_placeholder(match: JobMatch):
    from app.domain.matching.models import Recommendation

    return Recommendation.CONSIDER if match.practical_fit >= 55 else Recommendation.SKIP


async def _run(user_id: str, limit: int) -> timedelta | None:
    async with session_scope() as session:
        matches = await MatchRepository(session).list_for_user(uuid.UUID(user_id))
    return await _enrich_matches(user_id, matches, limit)


@celery_app.task(name="enrich.enrich_top_matches", bind=True, max_retries=_MAX_CAPACITY_RETRIES)
def enrich_top_matches(self, user_id: str, limit: int = DEFAULT_BATCH) -> dict[str, int]:
    retry_after = asyncio.run(_run(user_id, limit))
    if retry_after is not None and self.request.retries < _MAX_CAPACITY_RETRIES:
        raise self.retry(countdown=retry_countdown(retry_after, self.request.retries))
    return {"reviewed": limit}


async def _run_one(user_id: str, canonical_job_id: str) -> timedelta | None:
    async with session_scope() as session:
        match = await MatchRepository(session).get_for_canonical_job(
            uuid.UUID(user_id), uuid.UUID(canonical_job_id)
        )
    if match is None:
        return None
    return await _enrich_matches(user_id, [match], limit=1)


@celery_app.task(name="enrich.enrich_match", bind=True, max_retries=_MAX_CAPACITY_RETRIES)
def enrich_match(self, user_id: str, canonical_job_id: str) -> dict[str, str]:
    """The interactive path: a user asked for this specific job to be looked at.
    It skips the ranking entirely — someone opening a vacancy and pressing the
    button is the strongest value-of-information signal there is."""
    retry_after = asyncio.run(_run_one(user_id, canonical_job_id))
    if retry_after is not None and self.request.retries < _MAX_CAPACITY_RETRIES:
        raise self.retry(countdown=retry_countdown(retry_after, self.request.retries))
    return {"canonical_job_id": canonical_job_id}


async def _run_all() -> list[str]:
    async with session_scope() as session:
        return [str(user_id) for user_id in await CandidateRepository(session).list_user_ids_with_profile()]


@celery_app.task(name="enrich.enrich_all_users")
def enrich_all_users() -> dict[str, int]:
    """Daily pass: every user with an analysed CV gets their most decision-changing
    matches reviewed, within that capability's budget."""
    user_ids = asyncio.run(_run_all())
    for user_id in user_ids:
        enrich_top_matches.delay(user_id)
    return {"users": len(user_ids)}
