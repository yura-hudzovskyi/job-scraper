from unittest.mock import patch

from app.config.settings import Settings
from app.integrations.ai.embeddings.factory import (
    _cached_sentence_transformer_provider,
    build_embedding_provider,
)


def _settings(embedding_model: str = "fake-model") -> Settings:
    return Settings(embedding_provider="sentence_transformers", embedding_model=embedding_model)  # type: ignore[arg-type]


def test_sentence_transformer_provider_is_built_once_per_model_name() -> None:
    """Regression guard for the fix that stopped every score_job_for_user task from
    reconstructing SentenceTransformer(model_name) — and re-hitting Hugging Face
    Hub's cache-validation endpoint — from scratch on every single call."""
    _cached_sentence_transformer_provider.cache_clear()
    with patch(
        "app.integrations.ai.embeddings.sentence_transformer_provider."
        "SentenceTransformerEmbeddingProvider"
    ) as mock_provider_cls:
        mock_provider_cls.side_effect = lambda model_name: object()

        first = build_embedding_provider(_settings())
        second = build_embedding_provider(_settings())

        assert first is second
        assert mock_provider_cls.call_count == 1
    _cached_sentence_transformer_provider.cache_clear()


def test_different_model_names_get_different_provider_instances() -> None:
    _cached_sentence_transformer_provider.cache_clear()
    with patch(
        "app.integrations.ai.embeddings.sentence_transformer_provider."
        "SentenceTransformerEmbeddingProvider"
    ) as mock_provider_cls:
        mock_provider_cls.side_effect = lambda model_name: object()

        first = build_embedding_provider(_settings("model-a"))
        second = build_embedding_provider(_settings("model-b"))

        assert first is not second
        assert mock_provider_cls.call_count == 2
    _cached_sentence_transformer_provider.cache_clear()
