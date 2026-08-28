"""Needs the optional `llm` extra (`pip install -e ".[llm]"`) and network access.
No real API key required — verifies request plumbing via OpenAI's real (and free)
auth-rejection path, not actual embeddings.
"""

import pytest

pytest.importorskip("openai")

import openai

from app.integrations.ai.embeddings.openai_provider import OpenAIEmbeddingProvider


@pytest.mark.asyncio
async def test_bogus_api_key_reaches_real_api_and_is_rejected() -> None:
    provider = OpenAIEmbeddingProvider(api_key="sk-fake-key-for-smoke-test")

    with pytest.raises(openai.AuthenticationError):
        await provider.embed(["hello"])
