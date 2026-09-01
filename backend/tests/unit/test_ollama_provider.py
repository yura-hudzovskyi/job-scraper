"""test_structured_completion_returns_validated_schema needs a local Ollama server
(`ollama serve`) with `llama3.1` pulled, and skips cleanly when none is reachable —
no Ollama install was available to verify this live in the environment this was
authored in; it's exercised for real the first time someone runs it with a server
up. Everything else here mocks the transport and needs no server.
"""

import httpx
import pytest
from pydantic import BaseModel

from app.integrations.ai.llm.ollama_provider import OllamaLLMProvider, OllamaModelNotFound

_BASE_URL = "http://localhost:11434"
_MODEL = "llama3.1"


def _ollama_reachable() -> bool:
    try:
        return httpx.get(f"{_BASE_URL}/api/tags", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


class _Dummy(BaseModel):
    answer: str


@pytest.mark.asyncio
@pytest.mark.skipif(not _ollama_reachable(), reason="no local Ollama server")
async def test_structured_completion_returns_validated_schema() -> None:
    provider = OllamaLLMProvider(_BASE_URL, _MODEL)
    result = await provider.structured_completion(
        "Reply with a JSON object where 'answer' is the string 'hello'.", _Dummy
    )
    assert isinstance(result.data, _Dummy)
    assert result.model_label == f"Ollama ({_MODEL})"


@pytest.mark.asyncio
async def test_a_404_response_raises_a_clear_model_not_found_error() -> None:
    # Regression test: Ollama returns a bare 404 for a model tag that was never
    # pulled — this used to surface as a generic httpx.HTTPStatusError, which
    # reads as "something's broken" rather than "run `ollama pull <model>`" when
    # it shows up in LlmReranker's/JobSkillExtractionService's degrade-gracefully
    # warning logs. No real Ollama server needed — the transport is mocked.
    def _respond_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'not-pulled:1b' not found"})

    provider = OllamaLLMProvider(_BASE_URL, "not-pulled:1b")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(_respond_404))

    with pytest.raises(OllamaModelNotFound, match="not-pulled:1b"):
        await provider.structured_completion("irrelevant", _Dummy)
