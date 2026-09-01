from app.config.settings import Settings
from app.integrations.ai.llm.factory import build_configured_llm_provider, build_job_llm_provider
from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider
from app.integrations.ai.llm.ollama_provider import OllamaLLMProvider


def _settings(**overrides: object) -> Settings:
    return Settings(llm_provider="ollama", llm_model="llama3.2:3b", **overrides)  # type: ignore[arg-type]


def test_no_override_uses_the_configured_default_model() -> None:
    provider = build_configured_llm_provider(_settings())

    assert isinstance(provider, OllamaLLMProvider)
    assert provider.model == "llama3.2:3b"


def test_model_override_replaces_the_configured_default_for_this_call() -> None:
    provider = build_configured_llm_provider(_settings(), model_override="qwen2.5:14b")

    assert isinstance(provider, OllamaLLMProvider)
    assert provider.model == "qwen2.5:14b"


def test_build_job_llm_provider_uses_its_own_ollama_model_without_groq() -> None:
    # Deliberately NOT llm_model — that one is what CV analysis/preferences
    # AI-fill fall back to (build_quality_llm_provider); the job pipeline's local
    # model is independently choosable, e.g. a small/fast model for volume here
    # while CV analysis keeps a bigger one, or the reverse.
    provider = build_job_llm_provider(
        _settings(groq_api_key=None, ollama_fallback_model="llama3.1:8b")
    )

    assert isinstance(provider, OllamaLLMProvider)
    assert provider.model == "llama3.1:8b"


def test_build_job_llm_provider_model_override_without_groq_replaces_its_ollama_model() -> None:
    provider = build_job_llm_provider(
        _settings(groq_api_key=None, ollama_fallback_model="llama3.1:8b"),
        model_override="qwen3.5:4b",
    )

    assert isinstance(provider, OllamaLLMProvider)
    assert provider.model == "qwen3.5:4b"


def test_build_job_llm_provider_falls_back_to_configured_provider_for_hosted_llm_provider() -> None:
    # openai/anthropic have no local-vs-hosted "fallback model" distinction to
    # make, so this case is the one that still goes through
    # build_configured_llm_provider as-is.
    provider = build_job_llm_provider(
        Settings(llm_provider="openai", llm_model="gpt-4o-mini", openai_api_key="sk-fake")  # type: ignore[arg-type]
    )

    from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

    assert isinstance(provider, OpenAILLMProvider)


def test_build_job_llm_provider_wraps_groq_with_the_ollama_fallback_model() -> None:
    provider = build_job_llm_provider(
        _settings(groq_api_key="gsk_fake", ollama_fallback_model="llama3.1:8b")
    )

    assert isinstance(provider, FallbackLLMProvider)
    fallback = provider._fallback  # type: ignore[attr-defined]
    assert isinstance(fallback, OllamaLLMProvider)
    assert fallback.model == "llama3.1:8b"


def test_build_job_llm_provider_model_override_replaces_the_ollama_fallback_model() -> None:
    provider = build_job_llm_provider(
        _settings(groq_api_key="gsk_fake", ollama_fallback_model="llama3.1:8b"),
        model_override="qwen2.5:14b",
    )

    assert isinstance(provider, FallbackLLMProvider)
    fallback = provider._fallback  # type: ignore[attr-defined]
    assert isinstance(fallback, OllamaLLMProvider)
    assert fallback.model == "qwen2.5:14b"
