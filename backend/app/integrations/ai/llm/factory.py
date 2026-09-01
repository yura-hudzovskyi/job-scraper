"""Builds LLMProviders from Settings. Four entry points, not one, because the
call sites in this app genuinely want different providers rather than one global
choice — see docs/matching-engine.md:

- build_quality_llm_provider: CV analysis and preferences AI-fill — low-volume,
  quality-matters-most call sites. Gemini's free tier first (if GEMINI_API_KEY is
  set), falling back to Ollama (llm_model) automatically the moment Gemini
  returns a 429 (rate/quota exceeded) — never on other errors, so a broken API
  key surfaces loudly instead of being silently masked. A GeminiCircuitBreaker
  (circuit_breaker.py) rides along: once a 429 is actually seen, every later call
  that same day skips straight to Ollama instead of re-trying Gemini and paying
  for the same failed round trip. Falls back to build_configured_llm_provider
  (whatever llm_provider says) when Gemini isn't configured.
- build_job_llm_provider: job skill extraction, AI matching (ai_matcher.py), and
  the "should I apply?" reranker — the job pipeline's own high-volume call sites,
  run per scraped job and per (job, user). Groq's free tier first (if
  GROQ_API_KEY is set) — fast enough to actually churn through a real backlog,
  unlike CPU-only Ollama for anything past ~8B parameters — falling back to a
  small local model (ollama_fallback_model, not llm_model: this needs to finish
  in reasonable time under Celery's concurrent load, not be the best quality
  model available locally) the moment Groq returns 429. A
  FixedCooldownCircuitBreaker rides along with a short cooldown, not
  GeminiCircuitBreaker's until-midnight one — see circuit_breaker.py for why.
  With LLM_PROVIDER=ollama and no GROQ_API_KEY, this runs on
  ollama_fallback_model directly (not llm_model) — the job pipeline's local
  model is independently choosable from CV analysis's, e.g. a small/fast model
  here for volume while CV analysis's own Ollama fallback stays on something
  bigger, or the reverse. Falls back to build_configured_llm_provider only for
  the openai/anthropic case, where that distinction doesn't apply.
- build_configured_llm_provider: whatever llm_provider says, no Gemini/Groq
  involved. The base case both call sites above fall back to when their
  preferred hosted provider isn't configured.

Returns None when the selected provider needs a credential that isn't set —
callers decide whether that's fatal (e.g. CV analysis) or something to degrade
gracefully around.

Anthropic/OpenAI/Gemini/Groq imports are deferred into their branches: those
SDKs are the optional [llm] extra (see pyproject.toml), and importing them
unconditionally at module level would break app startup for anyone who installed
without it, even if they configured Ollama (the always-available default). Groq
reuses the `openai` package (Groq's own documented OpenAI-compatible endpoint),
so it doesn't add a new dependency beyond what OpenAI support already needs.
"""

import redis.asyncio as redis

from app.config.settings import Settings
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.ollama_provider import OllamaLLMProvider


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


def build_configured_llm_provider(
    settings: Settings, model_override: str | None = None
) -> LLMProvider | None:
    """Whatever llm_provider says (ollama/openai/anthropic), no Gemini/Groq
    involved — used as-is by build_quality_llm_provider and build_job_llm_provider
    when their preferred hosted provider isn't configured.

    model_override lets a single call site (currently: the "rescore all vacancies"
    admin action, see app/api/routes/jobs.py's POST /rescore-all) use a different
    model than Settings.llm_model for that one run, without touching global config —
    e.g. comparing qwen2.5:14b against the configured default across the whole
    existing job backlog.
    """
    if settings.llm_provider == "ollama":
        return OllamaLLMProvider(
            settings.ollama_base_url,
            model_override or settings.llm_model,
            num_ctx=settings.ollama_num_ctx,
            timeout_seconds=settings.ollama_timeout_seconds,
        )

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return None
        from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

        return OpenAILLMProvider(settings.openai_api_key, model_override or settings.llm_model)

    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            return None
        from app.integrations.ai.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(settings.anthropic_api_key, model_override or settings.llm_model)

    return None


def build_quality_llm_provider(
    settings: Settings, model_override: str | None = None
) -> LLMProvider | None:
    """CV analysis and preferences AI-fill only — see the module docstring. Not
    used by the job pipeline (skill extraction, AI matching, reranking) anymore;
    see build_job_llm_provider for that."""
    if settings.gemini_api_key:
        from app.integrations.ai.llm.circuit_breaker import GeminiCircuitBreaker
        from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider
        from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider

        gemini = GeminiLLMProvider(settings.gemini_api_key, settings.gemini_model)
        ollama = OllamaLLMProvider(
            settings.ollama_base_url,
            model_override or settings.llm_model,
            num_ctx=settings.ollama_num_ctx,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        circuit_breaker = GeminiCircuitBreaker(
            redis.from_url(settings.redis_url), settings.gemini_model
        )
        return FallbackLLMProvider(
            gemini, ollama, is_retryable=_is_gemini_rate_limited, circuit_breaker=circuit_breaker
        )

    return build_configured_llm_provider(settings, model_override)


def build_job_llm_provider(
    settings: Settings, model_override: str | None = None
) -> LLMProvider | None:
    """Job skill extraction (both the automatic per-scrape run and "rescore all
    vacancies"), AI matching, and the "should I apply?" reranker — see the module
    docstring. model_override, same meaning as build_configured_llm_provider's,
    overrides the *Ollama leg's* model for one run (e.g. "rescore all vacancies"
    comparing a different local model), not Groq's — Groq's model is always
    Settings.groq_model.

    Deliberately does NOT fall through to build_configured_llm_provider's
    Settings.llm_model when llm_provider is "ollama": that setting is what CV
    analysis/preferences AI-fill fall back to (build_quality_llm_provider), and
    the two are meant to stay independently choosable — e.g. running a small,
    fast model here (ollama_fallback_model) for job-pipeline volume while CV
    analysis's rare Gemini-fallback still gets a bigger/better one via llm_model,
    or vice versa. Only the openai/anthropic branches (no separate "fallback
    model" concept — there's no local-vs-hosted distinction to make there) still
    go through build_configured_llm_provider as-is.
    """
    if settings.groq_api_key:
        from app.integrations.ai.llm.circuit_breaker import FixedCooldownCircuitBreaker
        from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider
        from app.integrations.ai.llm.groq_provider import GroqLLMProvider

        groq = GroqLLMProvider(settings.groq_api_key, settings.groq_model)
        ollama = OllamaLLMProvider(
            settings.ollama_base_url,
            model_override or settings.ollama_fallback_model,
            num_ctx=settings.ollama_num_ctx,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
        circuit_breaker = FixedCooldownCircuitBreaker(
            redis.from_url(settings.redis_url),
            key=f"groq_exhausted:{settings.groq_model}",
            cooldown_seconds=settings.groq_circuit_breaker_cooldown_seconds,
        )
        return FallbackLLMProvider(
            groq, ollama, is_retryable=_is_groq_rate_limited, circuit_breaker=circuit_breaker
        )

    if settings.llm_provider == "ollama":
        return OllamaLLMProvider(
            settings.ollama_base_url,
            model_override or settings.ollama_fallback_model,
            num_ctx=settings.ollama_num_ctx,
            timeout_seconds=settings.ollama_timeout_seconds,
        )

    return build_configured_llm_provider(settings, model_override)
