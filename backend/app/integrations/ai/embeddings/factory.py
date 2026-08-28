"""Builds the configured EmbeddingProvider from Settings. Returns None when the
selected provider needs a credential that isn't set.

sentence-transformers/openai imports are deferred into their branches — both are
optional extras (see pyproject.toml) and must not break app startup for whichever
one isn't installed.
"""

from app.config.settings import Settings
from app.integrations.ai.embeddings.base import EmbeddingProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if settings.embedding_provider == "sentence_transformers":
        from app.integrations.ai.embeddings.sentence_transformer_provider import (
            SentenceTransformerEmbeddingProvider,
        )

        return SentenceTransformerEmbeddingProvider(settings.embedding_model)

    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            return None
        from app.integrations.ai.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(settings.openai_api_key, settings.embedding_model)

    return None
