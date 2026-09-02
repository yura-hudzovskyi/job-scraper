import pytest
import redis.asyncio as redis

from app.repositories.ai_settings_repository import AiSettingsRepository, UnknownAiSettingField


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def hset(self, key: str, field: str, value: str) -> None:
        self._hashes.setdefault(key, {})[field] = value

    async def hdel(self, key: str, field: str) -> None:
        self._hashes.get(key, {}).pop(field, None)


class _FailingRedis:
    async def hgetall(self, key: str) -> dict[str, str]:
        raise redis.RedisError("connection refused")

    async def hset(self, key: str, field: str, value: str) -> None:
        raise redis.RedisError("connection refused")

    async def hdel(self, key: str, field: str) -> None:
        raise redis.RedisError("connection refused")


@pytest.mark.asyncio
async def test_get_overrides_is_empty_before_anything_is_set() -> None:
    repository = AiSettingsRepository(_FakeRedis())  # type: ignore[arg-type]

    assert await repository.get_overrides() == {}


@pytest.mark.asyncio
async def test_set_override_is_reflected_in_get_overrides() -> None:
    repository = AiSettingsRepository(_FakeRedis())  # type: ignore[arg-type]

    await repository.set_override("groq_model", "openai/gpt-oss-120b")

    assert await repository.get_overrides() == {"groq_model": "openai/gpt-oss-120b"}


@pytest.mark.asyncio
async def test_set_override_with_none_clears_it() -> None:
    repository = AiSettingsRepository(_FakeRedis())  # type: ignore[arg-type]
    await repository.set_override("gemini_model", "gemini-2.0-pro")

    await repository.set_override("gemini_model", None)

    assert await repository.get_overrides() == {}


@pytest.mark.asyncio
async def test_set_override_with_empty_string_clears_it() -> None:
    repository = AiSettingsRepository(_FakeRedis())  # type: ignore[arg-type]
    await repository.set_override("groq_model", "llama-3.1-8b-instant")

    await repository.set_override("groq_model", "")

    assert await repository.get_overrides() == {}


@pytest.mark.asyncio
async def test_set_override_rejects_an_unknown_field() -> None:
    repository = AiSettingsRepository(_FakeRedis())  # type: ignore[arg-type]

    with pytest.raises(UnknownAiSettingField):
        await repository.set_override("embedding_model", "all-MiniLM-L6-v2")


@pytest.mark.asyncio
async def test_get_overrides_fails_open_on_a_redis_error() -> None:
    # An optional UI-config layer must never itself block or crash a call the
    # .env-configured default could still serve.
    repository = AiSettingsRepository(_FailingRedis())  # type: ignore[arg-type]

    assert await repository.get_overrides() == {}


@pytest.mark.asyncio
async def test_set_override_propagates_a_redis_error() -> None:
    # Unlike reads, a "Save" the user explicitly triggered must not silently
    # report success when it didn't actually persist.
    repository = AiSettingsRepository(_FailingRedis())  # type: ignore[arg-type]

    with pytest.raises(redis.RedisError):
        await repository.set_override("groq_model", "openai/gpt-oss-120b")
