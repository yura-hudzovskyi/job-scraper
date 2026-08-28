"""Builds the configured LLMProvider from Settings. Returns None when the selected
provider needs a credential that isn't set — callers decide whether that's fatal
(e.g. CV analysis) or something to degrade gracefully around.

Anthropic/OpenAI imports are deferred into their branches: those SDKs are the
optional [llm] extra (see pyproject.toml), and importing them unconditionally at
module level would break app startup for anyone who installed without it, even if
they configured Ollama (the always-available default).
"""

from app.config.settings import Settings
from app.integrations.ai.llm.base import LLMProvider
from app.integrations.ai.llm.ollama_provider import OllamaLLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider | None:
    if settings.llm_provider == "ollama":
        return OllamaLLMProvider(settings.ollama_base_url, settings.llm_model)

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return None
        from app.integrations.ai.llm.openai_provider import OpenAILLMProvider

        return OpenAILLMProvider(settings.openai_api_key, settings.llm_model)

    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            return None
        from app.integrations.ai.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(settings.anthropic_api_key, settings.llm_model)

    return None
