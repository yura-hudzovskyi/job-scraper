import pytest
import redis.asyncio as redis

from app.config.runtime_settings import get_effective_settings
from app.config.settings import Settings


class _FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._hash = dict(initial or {})

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hash)


class _FailingRedis:
    async def hgetall(self, key: str) -> dict[str, str]:
        raise redis.RedisError("connection refused")


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_returns_settings_unchanged_when_no_overrides_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(groq_model="llama-3.3-70b-versatile")
    monkeypatch.setattr(
        "app.config.runtime_settings.redis.from_url", lambda *a, **k: _FakeRedis()
    )

    effective = await get_effective_settings(settings)

    assert effective is settings


@pytest.mark.asyncio
async def test_overridden_fields_are_replaced_others_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(groq_model="llama-3.3-70b-versatile", gemini_model="gemini-2.0-flash")
    monkeypatch.setattr(
        "app.config.runtime_settings.redis.from_url",
        lambda *a, **k: _FakeRedis({"groq_model": "openai/gpt-oss-120b"}),
    )

    effective = await get_effective_settings(settings)

    assert effective.groq_model == "openai/gpt-oss-120b"
    assert effective.gemini_model == "gemini-2.0-flash"
    assert settings.groq_model == "llama-3.3-70b-versatile"  # original untouched


@pytest.mark.asyncio
async def test_fails_open_to_the_original_settings_on_a_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(
        "app.config.runtime_settings.redis.from_url", lambda *a, **k: _FailingRedis()
    )

    effective = await get_effective_settings(settings)

    assert effective is settings
