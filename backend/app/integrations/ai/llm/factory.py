"""Builds LLMProviders from Settings. Three entry points, not one, because the
call sites in this app genuinely want different providers rather than one global
choice — see docs/matching-engine.md:

- build_quality_llm_provider: CV analysis, AI job matching (ai_matcher.py) and the
  "should I apply?" reranker — everywhere quality/reasoning matters most. Gemini's
  free tier first (if GEMINI_API_KEY is set), falling back to Ollama automatically
  the moment Gemini returns a 429 (rate/quota exceeded) — never on other errors,
  so a broken API key surfaces loudly instead of being silently masked. This is
  also what "Gemini by default, local Ollama once we hit limits" actually means in
  practice for every one of these call sites — no separate budget/quota plumbing
  needed on top. A GeminiCircuitBreaker (circuit_breaker.py) rides along: once a
  429 is actually seen, every later call this same day skips straight to Ollama
  instead of re-trying Gemini and paying for the same failed round trip on every
  job scored. Falls back to build_configured_llm_provider (whatever llm_provider
  says) when Gemini isn't configured, so existing Ollama/OpenAI/Anthropic setups
  are unaffected.
- build_bulk_llm_provider: automatic job skill extraction, runs once per
  newly-scraped job (workers/tasks/extract_job_skills.py). Always Ollama,
  unconditionally — this keeps Gemini's limited free-tier quota reserved for the
  call sites above. The user-triggered "rescore all vacancies" action
  (workers/tasks/backfill.py) re-extracts skills too, but through
  build_quality_llm_provider instead — it's an occasional, explicit action, not
  automatic per-scrape volume, so it can afford Gemini's quality the same way CV
  analysis does.
- build_configured_llm_provider: whatever llm_provider says, no Gemini involved.
  The base case build_quality_llm_provider falls back to when Gemini isn't
  configured.

Returns None when the selected provider needs a credential that isn't set —
callers decide whether that's fatal (e.g. CV analysis) or something to degrade
gracefully around.

Anthropic/OpenAI/Gemini imports are deferred into their branches: those SDKs are
the optional [llm] extra (see pyproject.toml), and importing them unconditionally
at module level would break app startup for anyone who installed without it, even
if they configured Ollama (the always-available default).
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


def build_configured_llm_provider(
    settings: Settings, model_override: str | None = None
) -> LLMProvider | None:
    """Whatever llm_provider says (ollama/openai/anthropic), no Gemini involved —
    used as-is by build_quality_llm_provider when Gemini isn't configured, and
    directly by AiMatcher's factory wiring (app/domain/matching/factory.py).

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
    if settings.gemini_api_key:
        from app.integrations.ai.llm.circuit_breaker import GeminiCircuitBreaker
        from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider
        from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider

        gemini = GeminiLLMProvider(settings.gemini_api_key, settings.gemini_model)
        ollama = OllamaLLMProvider(
            settings.ollama_base_url,
            model_override or settings.llm_model,
            num_ctx=settings.ollama_num_ctx,
        )
        circuit_breaker = GeminiCircuitBreaker(
            redis.from_url(settings.redis_url), settings.gemini_model
        )
        return FallbackLLMProvider(
            gemini, ollama, is_retryable=_is_gemini_rate_limited, circuit_breaker=circuit_breaker
        )

    return build_configured_llm_provider(settings, model_override)


def build_bulk_llm_provider(settings: Settings) -> LLMProvider:
    return OllamaLLMProvider(
        settings.ollama_base_url, settings.llm_model, num_ctx=settings.ollama_num_ctx
    )
