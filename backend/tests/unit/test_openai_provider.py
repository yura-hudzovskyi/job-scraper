"""Needs the optional `llm` extra (`pip install -e ".[llm]"`) and network access.
No real API key required — these verify request plumbing via OpenAI's real (and
free) auth-rejection path, not actual completions.
"""

import pytest

pytest.importorskip("openai")

import openai
from pydantic import BaseModel

from app.integrations.ai.llm.openai_provider import OpenAILLMProvider


class _Dummy(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_bogus_api_key_reaches_real_api_and_is_rejected() -> None:
    """Proves the client is constructed correctly and talks to the real endpoint —
    a malformed request would fail differently (400) than a bad key (401)."""
    provider = OpenAILLMProvider(api_key="sk-fake-key-for-smoke-test", model="gpt-4o")

    with pytest.raises(openai.AuthenticationError):
        await provider.structured_completion("Say hello", _Dummy)
