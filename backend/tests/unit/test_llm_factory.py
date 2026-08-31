from app.config.settings import Settings
from app.integrations.ai.llm.factory import build_configured_llm_provider
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
