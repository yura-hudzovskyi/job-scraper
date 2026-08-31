import pytest
import redis.asyncio as redis

from app.integrations.ai.llm.circuit_breaker import GeminiCircuitBreaker


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_ex: int | None = None

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.last_ex = ex


class _FailingRedis:
    async def exists(self, key: str) -> int:
        raise redis.RedisError("connection refused")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise redis.RedisError("connection refused")


@pytest.mark.asyncio
async def test_is_open_is_false_before_any_failure_recorded() -> None:
    breaker = GeminiCircuitBreaker(_FakeRedis(), model="gemini-2.0-flash")  # type: ignore[arg-type]

    assert await breaker.is_open() is False


@pytest.mark.asyncio
async def test_record_failure_opens_the_breaker_with_a_positive_cooldown() -> None:
    redis_client = _FakeRedis()
    breaker = GeminiCircuitBreaker(redis_client, model="gemini-2.0-flash")  # type: ignore[arg-type]

    await breaker.record_failure()

    assert await breaker.is_open() is True
    assert redis_client.last_ex is not None
    assert redis_client.last_ex > 0


@pytest.mark.asyncio
async def test_breaker_is_keyed_per_model() -> None:
    redis_client = _FakeRedis()
    flash_breaker = GeminiCircuitBreaker(redis_client, model="gemini-2.0-flash")  # type: ignore[arg-type]
    pro_breaker = GeminiCircuitBreaker(redis_client, model="gemini-2.0-pro")  # type: ignore[arg-type]

    await flash_breaker.record_failure()

    assert await flash_breaker.is_open() is True
    assert await pro_breaker.is_open() is False


@pytest.mark.asyncio
async def test_fails_open_on_a_redis_error() -> None:
    # An optional cost-saving layer must never itself block a call the primary
    # might still be able to serve — a Redis hiccup must never look like an
    # exhausted quota.
    breaker = GeminiCircuitBreaker(_FailingRedis(), model="gemini-2.0-flash")  # type: ignore[arg-type]

    assert await breaker.is_open() is False
    await breaker.record_failure()  # must not raise
