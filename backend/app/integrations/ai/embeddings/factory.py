"""Builds the configured EmbeddingProvider from Settings. Returns None when the
selected provider needs a credential that isn't set.

sentence-transformers/openai imports are deferred into their branches — both are
optional extras (see pyproject.toml) and must not break app startup for whichever
one isn't installed.
"""

from functools import lru_cache

from app.config.settings import Settings
from app.integrations.ai.embeddings.base import CrossEncoderProvider, EmbeddingProvider


@lru_cache(maxsize=4)
def _cached_sentence_transformer_provider(model_name: str) -> EmbeddingProvider:
    """Every score_job_for_user Celery task calls build_matching_service ->
    build_embedding_provider fresh (see app/workers/tasks/score.py) — without this
    cache, that reconstructs SentenceTransformer(model_name) from scratch per job
    scored: a full model reload off disk, *and* a Hugging Face Hub cache-validation
    network round trip per file in the model repo (config.json,
    sentence_bert_config.json, tokenizer files, weights, ...) every single time,
    even though the model was already downloaded and never changes at runtime —
    directly contradicting the "local, no per-request cost" framing in
    docs/matching-engine.md. lru_cache keeps one loaded model per worker process
    instead, reused across every task that process ever picks up; encode() is
    CPU-bound, read-only inference, so sharing one instance across concurrent calls
    (run via asyncio.to_thread) is safe.
    """
    from app.integrations.ai.embeddings.sentence_transformer_provider import (
        SentenceTransformerEmbeddingProvider,
    )

    return SentenceTransformerEmbeddingProvider(model_name)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if settings.embedding_provider == "sentence_transformers":
        return _cached_sentence_transformer_provider(settings.embedding_model)

    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            return None
        from app.integrations.ai.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(settings.openai_api_key, settings.embedding_model)

    return None


@lru_cache(maxsize=4)
def _cached_cross_encoder_provider(model_name: str) -> CrossEncoderProvider:
    """Same rationale as _cached_sentence_transformer_provider above — one loaded
    CrossEncoder per worker process, reused across every job scored."""
    from app.integrations.ai.embeddings.cross_encoder_provider import (
        SentenceTransformersCrossEncoderProvider,
    )

    return SentenceTransformersCrossEncoderProvider(model_name)


def build_cross_encoder_provider(settings: Settings) -> CrossEncoderProvider | None:
    if not settings.cross_encoder_model:
        return None
    return _cached_cross_encoder_provider(settings.cross_encoder_model)
