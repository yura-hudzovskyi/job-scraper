"""Builds LLMProviders from Settings. Three entry points, not one, because the
call sites in this app want different provider orders rather than one global
choice — see docs/ai-pipeline-v3.md (F5, capability-specific routing):

- build_quality_llm_provider: CV analysis and preferences AI-fill — low-volume,
  quality-matters-most. Gemini first, Groq as the fallback leg.
- build_job_llm_provider: job skill extraction and the "should I apply?"
  reranker — the high-volume, throughput-matters call sites. Groq first (fast
  enough to churn through a real backlog), Gemini as the fallback leg.
- build_configured_llm_provider: the optional paid OpenAI/Anthropic leg
  (LLM_PROVIDER + LLM_MODEL). Off by default; used as the last fallback when a
  free tier isn't configured.

A chain falls back only on a *rate-limit* failure of its primary — a real
auth/config error surfaces loudly instead of being masked by a fallback that
hides a broken API key. A circuit breaker rides along so a primary that already
answered 429 is skipped for a cooldown instead of being re-tried on every single
call (see circuit_breaker.py).

Returns None when nothing is configured — callers decide whether that's fatal
(CV analysis) or something to degrade around (the job pipeline still scores
deterministically without an LLM).

Phase 3 of docs/ai-pipeline-v3.md replaces these hand-wired chains with a
capability router driven by stored model policy; until then this stays
deliberately small.

Vendor SDK imports are deferred into their branches: they are the optional [llm]
extra (see pyproject.toml), and importing them unconditionally would break app
startup for anyone who installed without it. Groq reuses the `openai` package
(Groq's own documented OpenAI-compatible endpoint), so it adds no new dependency.
"""

import redis.asyncio as redis

from app.config.settings import Settings
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider


def _is_gemini_rate_limited(exc: Exception) -> bool:
    # google.genai.errors.ClientError.code carries the HTTP status (verified
    # empirically against google-genai 2.20.0 — a bad API key raises the same
    # exception class with code=400, so checking the code specifically, not just
    # the exception type, is what keeps that case from being silently swallowed.
    return getattr(exc, "code", None) == 429


def _is_groq_rate_limited(exc: Exception) -> bool:
    # openai.APIStatusError (the base class for openai.RateLimitError, raised for
    # any OpenAI-compatible endpoint including Groq's) exposes .status_code —
    # checking that directly, duck-typed, avoids importing the openai SDK in this
    # module just for this predicate when Groq isn't even configured.
    return getattr(exc, "status_code", None) == 429


def _build_gemini(settings: Settings) -> LLMProvider | None:
    if not settings.gemini_api_key:
        return None
    from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider

    return GeminiLLMProvider(settings.gemini_api_key, settings.gemini_model)


def _build_groq(settings: Settings) -> LLMProvider | None:
    if not settings.groq_api_key:
        return None
    from app.integrations.ai.llm.groq_provider import GroqLLMProvider

    return GroqLLMProvider(settings.groq_api_key, settings.groq_model)


def build_configured_llm_provider(settings: Settings) -> LLMProvider | None:
    """The optional paid leg — None unless LLM_PROVIDER, LLM_MODEL and the
    matching API key are all set."""
    if settings.llm_provider is None or not settings.llm_model:
        return None

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return None
        from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

        return OpenAILLMProvider(settings.openai_api_key, settings.llm_model)

    if not settings.anthropic_api_key:
        return None
    from app.integrations.ai.llm.anthropic_provider import AnthropicLLMProvider

    return AnthropicLLMProvider(settings.anthropic_api_key, settings.llm_model)


def build_quality_llm_provider(settings: Settings) -> LLMProvider | None:
    """CV analysis and preferences AI-fill: Gemini first, Groq (then the optional
    paid leg) as the fallback — see the module docstring."""
    gemini = _build_gemini(settings)
    fallback = _build_groq(settings) or build_configured_llm_provider(settings)
    if gemini is None:
        return fallback
    if fallback is None:
        return gemini

    from app.integrations.ai.llm.circuit_breaker import GeminiCircuitBreaker

    return FallbackLLMProvider(
        gemini,
        fallback,
        is_retryable=_is_gemini_rate_limited,
        circuit_breaker=GeminiCircuitBreaker(
            redis.from_url(settings.redis_url), settings.gemini_model
        ),
    )


def build_job_llm_provider(settings: Settings) -> LLMProvider | None:
    """Job skill extraction and the "should I apply?" reranker: Groq first,
    Gemini (then the optional paid leg) as the fallback — see the module
    docstring."""
    groq = _build_groq(settings)
    fallback = _build_gemini(settings) or build_configured_llm_provider(settings)
    if groq is None:
        return fallback
    if fallback is None:
        return groq

    from app.integrations.ai.llm.circuit_breaker import FixedCooldownCircuitBreaker

    return FallbackLLMProvider(
        groq,
        fallback,
        is_retryable=_is_groq_rate_limited,
        circuit_breaker=FixedCooldownCircuitBreaker(
            redis.from_url(settings.redis_url),
            key=f"groq_exhausted:{settings.groq_model}",
            cooldown_seconds=settings.groq_circuit_breaker_cooldown_seconds,
        ),
    )
