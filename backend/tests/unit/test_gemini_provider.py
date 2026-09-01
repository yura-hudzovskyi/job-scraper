"""Needs the optional `llm` extra (`pip install -e ".[llm]"`) and network access.
No real API key required — verifies request plumbing via Gemini's real (and free)
auth-rejection path, not actual completions. Exception shape (ClientError with a
`.code` int) confirmed empirically against google-genai 2.20.0.
"""

import pytest

pytest.importorskip("google.genai")

from google.genai.errors import ClientError
from pydantic import BaseModel

from app.integrations.ai.llm.gemini_provider import GeminiLLMProvider


class _Dummy(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_bogus_api_key_reaches_real_api_and_is_rejected() -> None:
    """Proves the client is constructed correctly and talks to the real endpoint —
    a malformed request would fail differently than a bad key (400, INVALID_ARGUMENT)."""
    provider = GeminiLLMProvider(api_key="fake-key-for-smoke-test", model="gemini-2.0-flash")

    with pytest.raises(ClientError) as exc_info:
        await provider.structured_completion("Say hello", _Dummy)

    assert exc_info.value.code == 400
