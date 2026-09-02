"""Builds an LlmRouter for one capability from Settings.

Call sites ask for a capability — "extract a profile", "read a job posting",
"enrich a match" — and get something that satisfies the LLMProvider protocol.
Which vendors serve it, in what order, is policy (routing/policy.py); how a
failure is handled is the router's (routing/router.py); this module only turns
configuration into legs.

Returns None when nothing is configured at all. Callers decide whether that's
fatal (CV analysis) or something to degrade around (the job pipeline falls back
to rules, matching stays deterministic).

Vendor SDK imports stay deferred inside each leg's builder: they are the optional
[llm] extra (see pyproject.toml), importing them unconditionally would break
startup for anyone who installed without it, and a leg that never runs shouldn't
pay for its import. Groq reuses the `openai` package (its own documented
OpenAI-compatible endpoint), so it adds no new dependency.
"""

from collections.abc import Callable

import redis.asyncio as redis

from app.config.settings import Settings
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.quota.budget import DailyCapabilityBudget
from app.integrations.ai.quota.ledger import InvocationLog
from app.integrations.ai.routing import policy
from app.integrations.ai.routing.router import Capability, LlmRouter, ModelLeg
from app.integrations.ai.routing.state import ProviderStateStore


def _gemini_leg(settings: Settings) -> ModelLeg | None:
    if not settings.gemini_api_key:
        return None
    api_key, model = settings.gemini_api_key, settings.gemini_model

    def build() -> LLMProvider:
        from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider

        return GeminiLLMProvider(api_key, model)

    return ModelLeg(provider=policy.GEMINI, model=model, build=build)


def _groq_leg(settings: Settings) -> ModelLeg | None:
    if not settings.groq_api_key:
        return None
    api_key, model = settings.groq_api_key, settings.groq_model

    def build() -> LLMProvider:
        from app.integrations.ai.llm.groq_provider import GroqLLMProvider

        return GroqLLMProvider(api_key, model)

    return ModelLeg(provider=policy.GROQ, model=model, build=build)


def _paid_leg(settings: Settings) -> ModelLeg | None:
    """The optional OpenAI/Anthropic leg — only when the provider, the model and
    the matching key are all set."""
    model = settings.llm_model
    if settings.llm_provider is None or not model:
        return None

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return None
        openai_key = settings.openai_api_key

        def build_openai() -> LLMProvider:
            from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

            return OpenAILLMProvider(openai_key, model)

        return ModelLeg(provider=policy.PAID, model=model, build=build_openai)

    if not settings.anthropic_api_key:
        return None
    anthropic_key = settings.anthropic_api_key

    def build_anthropic() -> LLMProvider:
        from app.integrations.ai.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(anthropic_key, model)

    return ModelLeg(provider=policy.PAID, model=model, build=build_anthropic)


_BUILDERS: dict[str, Callable[[Settings], ModelLeg | None]] = {
    policy.GEMINI: _gemini_leg,
    policy.GROQ: _groq_leg,
    policy.PAID: _paid_leg,
}


def legs_for(capability: Capability, settings: Settings) -> list[ModelLeg]:
    """The configured legs for this capability, in policy order. A provider with
    no credentials is simply absent rather than a leg that always fails."""
    legs = (_BUILDERS[name](settings) for name in policy.provider_order(capability))
    return [leg for leg in legs if leg is not None]


def build_llm_router(capability: Capability, settings: Settings) -> LlmRouter | None:
    legs = legs_for(capability, settings)
    if not legs:
        return None

    client = redis.from_url(settings.redis_url)
    return LlmRouter(
        capability,
        legs,
        ProviderStateStore(client),
        DailyCapabilityBudget(
            client, capability.value, policy.daily_limit(capability, settings)
        ),
        InvocationLog(client),
    )
