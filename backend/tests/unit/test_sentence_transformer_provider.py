"""Needs the optional `matching` extra (`pip install -e ".[matching]"`) and a network
connection on first run to download the model. Skips cleanly without either.
"""

import pytest

pytest.importorskip("sentence_transformers")

from app.integrations.ai.embeddings.sentence_transformer_provider import (
    SentenceTransformerEmbeddingProvider,
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b)


@pytest.mark.asyncio
async def test_similar_texts_score_higher_than_unrelated_ones() -> None:
    provider = SentenceTransformerEmbeddingProvider("all-MiniLM-L6-v2")
    vectors = await provider.embed(
        [
            "Senior Python backend engineer, FastAPI, PostgreSQL, Docker",
            "Experienced Python developer building REST APIs with FastAPI and Postgres",
            "Graphic designer skilled in Adobe Photoshop and Illustrator",
        ]
    )

    assert len(vectors[0]) == 384  # all-MiniLM-L6-v2's dimensionality
    similar = _cosine(vectors[0], vectors[1])
    unrelated = _cosine(vectors[0], vectors[2])
    assert similar > unrelated
