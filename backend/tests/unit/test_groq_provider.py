"""Needs the optional `llm` extra (`pip install -e ".[llm]"`) and network access.
No real API key required — verifies request plumbing via Groq's real (and free)
auth-rejection path, not actual completions. Groq is accessed through the `openai`
SDK pointed at Groq's OpenAI-compatible endpoint, so the same exception shape
(openai.AuthenticationError) applies as OpenAILLMProvider's own test.
"""

import pytest

pytest.importorskip("openai")

import openai
from pydantic import BaseModel

from app.integrations.ai.llm.groq_provider import GroqLLMProvider


class _Dummy(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_bogus_api_key_reaches_real_groq_endpoint_and_is_rejected() -> None:
    """Proves the client is actually pointed at Groq's endpoint and talks to it —
    a malformed request would fail differently (400) than a bad key (401)."""
    provider = GroqLLMProvider(api_key="gsk_fake-key-for-smoke-test", model="llama-3.3-70b-versatile")

    with pytest.raises(openai.AuthenticationError):
        await provider.structured_completion("Say hello", _Dummy)
