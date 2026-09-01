"""Provider-chain wiring only, no network: each entry point orders the same two
free tiers differently, and the paid OpenAI/Anthropic leg is off unless it is
fully configured. See app/integrations/ai/llm/factory.py.
"""

from app.config.settings import Settings
from app.integrations.ai.llm.factory import (
    build_configured_llm_provider,
    build_job_llm_provider,
    build_quality_llm_provider,
)
from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider
from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider
from app.integrations.ai.llm.groq_provider import GroqLLMProvider


def _settings(**overrides: object) -> Settings:
    """Every credential explicitly unset, so a real key in the ambient
    environment can't change what these tests build."""
    base: dict[str, object] = {
        "groq_api_key": None,
        "gemini_api_key": None,
        "openai_api_key": None,
        "anthropic_api_key": None,
        "llm_provider": None,
        "llm_model": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_nothing_configured_builds_no_provider() -> None:
    settings = _settings()

    assert build_job_llm_provider(settings) is None
    assert build_quality_llm_provider(settings) is None
    assert build_configured_llm_provider(settings) is None


def test_job_pipeline_runs_on_groq_alone_when_it_is_the_only_leg() -> None:
    provider = build_job_llm_provider(_settings(groq_api_key="gsk_fake"))

    assert isinstance(provider, GroqLLMProvider)


def test_job_pipeline_falls_back_from_groq_to_gemini() -> None:
    provider = build_job_llm_provider(
        _settings(groq_api_key="gsk_fake", gemini_api_key="AIza_fake")
    )

    assert isinstance(provider, FallbackLLMProvider)
    assert isinstance(provider._primary, GroqLLMProvider)  # type: ignore[attr-defined]
    assert isinstance(provider._fallback, GeminiLLMProvider)  # type: ignore[attr-defined]


def test_job_pipeline_runs_on_gemini_when_groq_is_not_configured() -> None:
    provider = build_job_llm_provider(_settings(gemini_api_key="AIza_fake"))

    assert isinstance(provider, GeminiLLMProvider)


def test_quality_pipeline_falls_back_from_gemini_to_groq() -> None:
    # The reverse order of the job pipeline: CV analysis is low-volume and wants
    # the better model first, the job pipeline is high-volume and wants the fast
    # one first.
    provider = build_quality_llm_provider(
        _settings(groq_api_key="gsk_fake", gemini_api_key="AIza_fake")
    )

    assert isinstance(provider, FallbackLLMProvider)
    assert isinstance(provider._primary, GeminiLLMProvider)  # type: ignore[attr-defined]
    assert isinstance(provider._fallback, GroqLLMProvider)  # type: ignore[attr-defined]


def test_quality_pipeline_runs_on_gemini_alone_when_groq_is_not_configured() -> None:
    provider = build_quality_llm_provider(_settings(gemini_api_key="AIza_fake"))

    assert isinstance(provider, GeminiLLMProvider)


def test_the_paid_leg_needs_a_provider_a_model_and_its_key() -> None:
    assert build_configured_llm_provider(_settings(llm_provider="openai")) is None
    assert (
        build_configured_llm_provider(_settings(llm_provider="openai", llm_model="gpt-4o-mini"))
        is None
    )

    provider = build_configured_llm_provider(
        _settings(llm_provider="openai", llm_model="gpt-4o-mini", openai_api_key="sk-fake")
    )

    from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

    assert isinstance(provider, OpenAILLMProvider)


def test_the_paid_leg_is_the_fallback_when_only_one_free_tier_is_configured() -> None:
    from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

    provider = build_job_llm_provider(
        _settings(
            groq_api_key="gsk_fake",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            openai_api_key="sk-fake",
        )
    )

    assert isinstance(provider, FallbackLLMProvider)
    assert isinstance(provider._fallback, OpenAILLMProvider)  # type: ignore[attr-defined]
