import pytest

from app.config.settings import Settings
from app.integrations.notifications.telegram_webhook import (
    register_webhook,
    resolve_webhook_secret,
)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "telegram_bot_token": "123:abc",
        "api_domain": "example.duckdns.org",
        "secret_key": "super-secret",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_explicit_webhook_secret_is_used_as_is() -> None:
    settings = _settings(telegram_webhook_secret="my-explicit-secret")

    assert resolve_webhook_secret(settings) == "my-explicit-secret"


def test_secret_is_derived_from_secret_key_when_unset() -> None:
    settings = _settings(telegram_webhook_secret=None)

    derived = resolve_webhook_secret(settings)

    assert derived
    assert derived == resolve_webhook_secret(_settings(telegram_webhook_secret=None))


def test_derived_secret_changes_with_the_secret_key() -> None:
    a = resolve_webhook_secret(_settings(secret_key="key-a", telegram_webhook_secret=None))
    b = resolve_webhook_secret(_settings(secret_key="key-b", telegram_webhook_secret=None))

    assert a != b


@pytest.mark.asyncio
async def test_register_webhook_is_a_noop_without_a_bot_token() -> None:
    settings = _settings(telegram_bot_token=None)

    await register_webhook(settings)  # must not raise despite no real network access


@pytest.mark.asyncio
async def test_register_webhook_is_a_noop_without_a_public_domain() -> None:
    settings = _settings(api_domain=None)

    await register_webhook(settings)  # must not raise despite no real network access


@pytest.mark.asyncio
async def test_register_webhook_does_not_raise_when_telegram_rejects_it() -> None:
    # A bogus bot token means set_webhook will genuinely fail against the real
    # Telegram API — register_webhook must swallow that (log and return), not let
    # a bad/stale token block API startup.
    settings = _settings(telegram_bot_token="123456:fake-bot-token-for-smoke-test")

    await register_webhook(settings)
