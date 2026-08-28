"""Needs a local Ollama server (`ollama serve`) with `llama3.1` pulled. Skips cleanly
when none is reachable — no Ollama install was available to verify this live in the
environment this was authored in; this test exists so it's exercised for real the
first time someone runs it with a server up.
"""

import httpx
import pytest
from pydantic import BaseModel

from app.integrations.ai.llm.ollama_provider import DEFAULT_MODEL, OllamaLLMProvider

_BASE_URL = "http://localhost:11434"


def _ollama_reachable() -> bool:
    try:
        return httpx.get(f"{_BASE_URL}/api/tags", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _ollama_reachable(), reason="no local Ollama server")


class _Dummy(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_structured_completion_returns_validated_schema() -> None:
    provider = OllamaLLMProvider(_BASE_URL, DEFAULT_MODEL)
    result = await provider.structured_completion(
        "Reply with a JSON object where 'answer' is the string 'hello'.", _Dummy
    )
    assert isinstance(result, _Dummy)
