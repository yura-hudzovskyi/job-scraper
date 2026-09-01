"""Needs the optional `llm` extra (`pip install -e ".[llm]"`) and network access.
No real API key required — these verify request plumbing via Anthropic's real (and
free) auth-rejection path, not actual completions.
"""

import pytest

pytest.importorskip("anthropic")

import anthropic
from pydantic import BaseModel

from app.integrations.ai.llm.anthropic_provider import AnthropicLLMProvider


class _Dummy(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_bogus_api_key_reaches_real_api_and_is_rejected() -> None:
    """Proves the client is constructed correctly and talks to the real endpoint —
    a malformed request would fail differently (400) than a bad key (401)."""
    provider = AnthropicLLMProvider(
        api_key="sk-ant-api03-fake-key-for-smoke-test", model="claude-opus-5"
    )

    with pytest.raises(anthropic.AuthenticationError):
        await provider.structured_completion("Say hello", _Dummy)
