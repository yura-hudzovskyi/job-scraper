import pytest
from pydantic import BaseModel

from app.integrations.ai.llm.base import LLMResult
from app.integrations.ai.llm.fallback_provider import FallbackLLMProvider


class _Dummy(BaseModel):
    answer: str


class _RetryableError(Exception):
    pass


class _OtherError(Exception):
    pass


class _FakeProvider:
    def __init__(self, label: str, raises: Exception | None = None):
        self._label = label
        self._raises = raises
        self.calls = 0

    async def structured_completion(self, prompt: str, schema: type) -> LLMResult:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return LLMResult(data=schema(answer="ok"), model_label=self._label)


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, _RetryableError)


@pytest.mark.asyncio
async def test_uses_primary_result_when_primary_succeeds() -> None:
    primary = _FakeProvider("primary")
    fallback = _FakeProvider("fallback")
    provider = FallbackLLMProvider(primary, fallback, is_retryable=_is_retryable)  # type: ignore[arg-type]

    result = await provider.structured_completion("prompt", _Dummy)

    assert result.model_label == "primary"
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_falls_back_when_primary_raises_a_retryable_error() -> None:
    primary = _FakeProvider("primary", raises=_RetryableError())
    fallback = _FakeProvider("fallback")
    provider = FallbackLLMProvider(primary, fallback, is_retryable=_is_retryable)  # type: ignore[arg-type]

    result = await provider.structured_completion("prompt", _Dummy)

    assert result.model_label == "fallback"
    assert primary.calls == 1


@pytest.mark.asyncio
async def test_propagates_a_non_retryable_error_without_falling_back() -> None:
    primary = _FakeProvider("primary", raises=_OtherError("bad api key"))
    fallback = _FakeProvider("fallback")
    provider = FallbackLLMProvider(primary, fallback, is_retryable=_is_retryable)  # type: ignore[arg-type]

    with pytest.raises(_OtherError):
        await provider.structured_completion("prompt", _Dummy)

    assert fallback.calls == 0
