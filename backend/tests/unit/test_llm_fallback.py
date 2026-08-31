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


class _FakeCircuitBreaker:
    def __init__(self, open_: bool = False):
        self.open = open_
        self.record_failure_calls = 0

    async def is_open(self) -> bool:
        return self.open

    async def record_failure(self) -> None:
        self.record_failure_calls += 1
        self.open = True


@pytest.mark.asyncio
async def test_skips_primary_entirely_when_the_circuit_breaker_is_already_open() -> None:
    primary = _FakeProvider("primary")
    fallback = _FakeProvider("fallback")
    breaker = _FakeCircuitBreaker(open_=True)
    provider = FallbackLLMProvider(
        primary, fallback, is_retryable=_is_retryable, circuit_breaker=breaker  # type: ignore[arg-type]
    )

    result = await provider.structured_completion("prompt", _Dummy)

    assert result.model_label == "fallback"
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_a_retryable_failure_opens_the_circuit_breaker() -> None:
    primary = _FakeProvider("primary", raises=_RetryableError())
    fallback = _FakeProvider("fallback")
    breaker = _FakeCircuitBreaker()
    provider = FallbackLLMProvider(
        primary, fallback, is_retryable=_is_retryable, circuit_breaker=breaker  # type: ignore[arg-type]
    )

    await provider.structured_completion("prompt", _Dummy)

    assert breaker.record_failure_calls == 1
    assert breaker.open is True


@pytest.mark.asyncio
async def test_a_non_retryable_failure_does_not_touch_the_circuit_breaker() -> None:
    primary = _FakeProvider("primary", raises=_OtherError("bad api key"))
    fallback = _FakeProvider("fallback")
    breaker = _FakeCircuitBreaker()
    provider = FallbackLLMProvider(
        primary, fallback, is_retryable=_is_retryable, circuit_breaker=breaker  # type: ignore[arg-type]
    )

    with pytest.raises(_OtherError):
        await provider.structured_completion("prompt", _Dummy)

    assert breaker.record_failure_calls == 0
