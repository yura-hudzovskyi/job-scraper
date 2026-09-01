"""Runtime AI model configuration for the System page — lets a user change which
model each tier uses (see app/config/runtime_settings.py) without touching .env or
restarting anything, and lets them test a model directly against its real provider
so a bad or deprecated model id (Groq's free-tier catalog changes over time — see
docs/matching-engine.md) surfaces immediately instead of silently degrading
through FallbackLLMProvider the next time a real job gets scored.

Deliberately scoped to *model names* only — LLM_PROVIDER/EMBEDDING_PROVIDER and
every API key stay .env-only: those are infra/secrets decisions, not something to
flip at runtime from an authenticated session.
"""

import logging
import uuid
from typing import Literal

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user_id
from app.config.runtime_settings import get_effective_settings
from app.config.settings import Settings, get_settings
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.circuit_breaker import (
    FixedCooldownCircuitBreaker,
    GeminiCircuitBreaker,
)
from app.repositories.ai_settings_repository import AiSettingsRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _ai_settings_repository(settings: Settings) -> AiSettingsRepository:
    return AiSettingsRepository(redis.from_url(settings.redis_url, decode_responses=True))


class ModelFieldStatus(BaseModel):
    value: str
    is_override: bool
    default: str


class AiModelsResponse(BaseModel):
    groq_configured: bool
    groq_model: ModelFieldStatus
    groq_circuit_open: bool
    gemini_configured: bool
    gemini_model: ModelFieldStatus
    gemini_circuit_open: bool


@router.get("/models", response_model=AiModelsResponse)
async def get_ai_models(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> AiModelsResponse:
    settings = get_settings()
    overrides = await _ai_settings_repository(settings).get_overrides()
    effective = await get_effective_settings(settings)

    def _field(name: str) -> ModelFieldStatus:
        return ModelFieldStatus(
            value=getattr(effective, name),
            is_override=name in overrides,
            default=getattr(settings, name),
        )

    groq_circuit_open = False
    if settings.groq_api_key:
        breaker = FixedCooldownCircuitBreaker(
            redis.from_url(settings.redis_url),
            key=f"groq_exhausted:{effective.groq_model}",
            cooldown_seconds=settings.groq_circuit_breaker_cooldown_seconds,
        )
        groq_circuit_open = await breaker.is_open()

    gemini_circuit_open = False
    if settings.gemini_api_key:
        gemini_breaker = GeminiCircuitBreaker(redis.from_url(settings.redis_url), effective.gemini_model)
        gemini_circuit_open = await gemini_breaker.is_open()

    return AiModelsResponse(
        groq_configured=bool(settings.groq_api_key),
        groq_model=_field("groq_model"),
        groq_circuit_open=groq_circuit_open,
        gemini_configured=bool(settings.gemini_api_key),
        gemini_model=_field("gemini_model"),
        gemini_circuit_open=gemini_circuit_open,
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
) -> AiModelsResponse:
    settings = get_settings()
    repository = _ai_settings_repository(settings)
    try:
        for field, value in payload.model_dump(exclude_unset=True).items():
            await repository.set_override(field, value)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"could not save — Redis unreachable: {exc}") from exc
    return await get_ai_models(user_id)


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
    FallbackLLMProvider on purpose, so a broken model surfaces its real error
    (e.g. Groq's own "model does not exist" message for a deprecated/mistyped
    model id) instead of the silent degrade-to-deterministic every other call
    site in this app deliberately does."""
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
