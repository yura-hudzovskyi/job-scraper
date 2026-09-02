"""Runtime AI configuration and status for the System page.

Two jobs: let a user change which model each tier uses (see
app/config/runtime_settings.py) without touching .env or restarting anything, and
show what the router is actually doing right now — which legs are serving traffic,
which are cooling down and why, and how much of each capability's daily budget is
left (see app/integrations/ai/routing/, app/integrations/ai/quota/budget.py).

The status half matters because every failure path in this app degrades quietly
by design: without it, "the AI stopped working" looks identical to "the AI is
fine but this job had nothing to extract". It also lets a user test a model
directly against its real provider, so a bad or deprecated model id surfaces
immediately instead of silently degrading the next time a real job is scored.

Deliberately scoped to *model names* only — LLM_PROVIDER/EMBEDDING_PROVIDER and
every API key stay .env-only: those are infra/secrets decisions, not something to
flip at runtime from an authenticated session.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import (
    get_ai_invocation_repository,
    get_current_user_id,
    get_embedding_repository,
    get_job_repository,
)
from app.config.runtime_settings import get_effective_settings
from app.config.settings import Settings, get_settings
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.factory import legs_for
from app.integrations.ai.quota.budget import DailyCapabilityBudget
from app.integrations.ai.routing import policy
from app.integrations.ai.routing.router import Capability
from app.integrations.ai.routing.state import ProviderStateStore
from app.repositories.ai_invocation_repository import AiInvocationRepository
from app.repositories.ai_settings_repository import AiSettingsRepository
from app.repositories.embedding_repository import JOB, EmbeddingRepository
from app.repositories.job_repository import JobRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _ai_settings_repository(settings: Settings) -> AiSettingsRepository:
    return AiSettingsRepository(redis.from_url(settings.redis_url, decode_responses=True))


class ModelFieldStatus(BaseModel):
    value: str
    is_override: bool
    default: str


class LegStatus(BaseModel):
    """One provider/model pair as the router currently sees it."""

    provider: str
    model: str
    available: bool
    # Why it isn't available: rate_limit, quota_exhausted, transient, fatal.
    reason: str | None = None
    retry_after_seconds: int | None = None


class CapabilityStatus(BaseModel):
    capability: str
    legs: list[LegStatus]
    budget_used: int
    budget_limit: int


class LaneStatus(BaseModel):
    """One embedding lane: its own vector space, and how much of the corpus it
    has actually indexed. A lane only answers queries once it covers nearly
    everything — see app/services/embedding_indexing_service.py."""

    id: str
    provider: str
    model: str
    dimension: int
    role: str
    state: str
    jobs_covered: int
    jobs_total: int


class AiModelsResponse(BaseModel):
    groq_configured: bool
    groq_model: ModelFieldStatus
    gemini_configured: bool
    gemini_model: ModelFieldStatus
    capabilities: list[CapabilityStatus]
    lanes: list[LaneStatus]


async def _capability_status(
    capability: Capability, settings: Settings, client: redis.Redis
) -> CapabilityStatus:
    state = ProviderStateStore(client)
    budget = DailyCapabilityBudget(
        client, capability.value, policy.daily_limit(capability, settings)
    )
    legs = []
    for leg in legs_for(capability, settings):
        leg_state = await state.state(leg.key)
        retry_after = leg_state.retry_after
        legs.append(
            LegStatus(
                provider=leg.provider,
                model=leg.model,
                available=leg_state.available,
                reason=leg_state.reason.value if leg_state.reason else None,
                retry_after_seconds=(
                    int(retry_after.total_seconds()) if retry_after is not None else None
                ),
            )
        )
    return CapabilityStatus(
        capability=capability.value,
        legs=legs,
        budget_used=await budget.used(),
        budget_limit=budget.daily_limit,
    )


async def _lane_statuses(
    embedding_repository: EmbeddingRepository, job_repository: JobRepository
) -> list[LaneStatus]:
    jobs_total = await job_repository.count_canonical_jobs()
    return [
        LaneStatus(
            id=lane.id,
            provider=lane.provider,
            model=lane.model,
            dimension=lane.dimension,
            role=lane.role,
            state=lane.state,
            jobs_covered=await embedding_repository.documents_with_vectors(lane.id, JOB),
            jobs_total=jobs_total,
        )
        for lane in await embedding_repository.list_lanes()
    ]


@router.get("/models", response_model=AiModelsResponse)
async def get_ai_models(
    user_id: uuid.UUID = Depends(get_current_user_id),
    embedding_repository: EmbeddingRepository = Depends(get_embedding_repository),
    job_repository: JobRepository = Depends(get_job_repository),
) -> AiModelsResponse:
    settings = get_settings()
    overrides = await _ai_settings_repository(settings).get_overrides()
    effective = await get_effective_settings(settings)
    client = redis.from_url(settings.redis_url)

    def _field(name: str) -> ModelFieldStatus:
        return ModelFieldStatus(
            value=getattr(effective, name),
            is_override=name in overrides,
            default=getattr(settings, name),
        )

    return AiModelsResponse(
        groq_configured=bool(settings.groq_api_key),
        groq_model=_field("groq_model"),
        gemini_configured=bool(settings.gemini_api_key),
        gemini_model=_field("gemini_model"),
        capabilities=[
            await _capability_status(capability, effective, client) for capability in Capability
        ],
        lanes=await _lane_statuses(embedding_repository, job_repository),
    )


class AiModelsUpdateRequest(BaseModel):
    # None or "" clears that field's override (falls back to .env again) — see
    # AiSettingsRepository.set_override. Omitting a field leaves it unchanged.
    groq_model: str | None = None
    gemini_model: str | None = None


@router.patch("/models", response_model=AiModelsResponse)
async def update_ai_models(
    payload: AiModelsUpdateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    embedding_repository: EmbeddingRepository = Depends(get_embedding_repository),
    job_repository: JobRepository = Depends(get_job_repository),
) -> AiModelsResponse:
    settings = get_settings()
    repository = _ai_settings_repository(settings)
    try:
        for field, value in payload.model_dump(exclude_unset=True).items():
            await repository.set_override(field, value)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"could not save — Redis unreachable: {exc}") from exc
    return await get_ai_models(user_id, embedding_repository, job_repository)


class UsageRow(BaseModel):
    capability: str
    outcome: str
    calls: int


class AiUsageResponse(BaseModel):
    """What the router actually did recently, from the durable ledger. Budgets
    say what is left today; this says where it went and how much of it failed."""

    since_hours: int
    rows: list[UsageRow]


@router.get("/usage", response_model=AiUsageResponse)
async def get_ai_usage(
    hours: int = 24,
    user_id: uuid.UUID = Depends(get_current_user_id),
    repository: AiInvocationRepository = Depends(get_ai_invocation_repository),
) -> AiUsageResponse:
    window = max(1, min(hours, 24 * 30))
    counts = await repository.count_since(datetime.now(UTC) - timedelta(hours=window))
    rows = [
        UsageRow(capability=capability, outcome=outcome, calls=calls)
        for (capability, outcome), calls in sorted(counts.items(), key=lambda item: -item[1])
    ]
    return AiUsageResponse(since_hours=window, rows=rows)


class TestModelRequest(BaseModel):
    tier: Literal["groq", "gemini"]
    model: str


class TestModelResponse(BaseModel):
    ok: bool
    model_label: str | None = None
    error: str | None = None


class _Probe(BaseModel):
    ok: bool


@router.post("/models/test", response_model=TestModelResponse)
async def test_ai_model(
    payload: TestModelRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> TestModelResponse:
    """Fires one real, minimal completion against the raw provider — bypassing
    the router on purpose, so a broken model surfaces its real error (e.g. Groq's
    own "model does not exist" message for a deprecated/mistyped model id)
    instead of being classified, parked and degraded around like every other call
    site here deliberately does."""
    settings = get_settings()
    provider: LLMProvider

    if payload.tier == "groq":
        if not settings.groq_api_key:
            return TestModelResponse(ok=False, error="GROQ_API_KEY is not set")
        from app.integrations.ai.llm.groq_provider import GroqLLMProvider

        provider = GroqLLMProvider(settings.groq_api_key, payload.model)
    else:
        if not settings.gemini_api_key:
            return TestModelResponse(ok=False, error="GEMINI_API_KEY is not set")
        from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider

        provider = GeminiLLMProvider(settings.gemini_api_key, payload.model)

    try:
        result = await provider.structured_completion(
            'Reply with exactly this JSON object and nothing else: {"ok": true}', _Probe
        )
    except Exception as exc:
        logger.warning("model test failed for %s/%s", payload.tier, payload.model, exc_info=True)
        return TestModelResponse(ok=False, error=str(exc))
    return TestModelResponse(ok=True, model_label=result.model_label)
