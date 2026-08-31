"""Registers this app's Telegram webhook at startup — see docs/notifications.md
for why a webhook, not polling, is used to receive Approve/Reject button taps.
"""

import hashlib
import logging

from app.config.settings import Settings
from app.integrations.notifications.telegram_provider import (
    TelegramApiError,
    TelegramNotificationProvider,
)

logger = logging.getLogger(__name__)


def resolve_webhook_secret(settings: Settings) -> str:
    """An explicit TELEGRAM_WEBHOOK_SECRET always wins; otherwise derive a
    stable, unguessable-without-secret_key one, so the webhook is never left
    unauthenticated just because nobody set one more env var. Used both to
    register the webhook (here) and to validate incoming calls (see
    api/routes/telegram.py's POST /webhook)."""
    if settings.telegram_webhook_secret:
        return settings.telegram_webhook_secret
    return hashlib.sha256(f"telegram-webhook:{settings.secret_key}".encode()).hexdigest()


async def register_webhook(settings: Settings) -> None:
    """Best-effort — logs and returns rather than raising, so a transient
    Telegram/DNS hiccup at container startup never blocks the API from serving
    everything else. No-ops when there's no bot token or no public domain
    configured (local dev), same "optional layer degrades gracefully" policy as
    everywhere else in this app. Safe to call on every startup: setWebhook is
    idempotent, and this app runs multiple uvicorn workers that will each call it
    once with the exact same arguments."""
    if not settings.telegram_bot_token or not settings.api_domain:
        return

    url = f"https://{settings.api_domain}/api/integrations/telegram/webhook"
    provider = TelegramNotificationProvider(settings.telegram_bot_token, chat_id="unused")
    try:
        await provider.set_webhook(url, resolve_webhook_secret(settings))
    except TelegramApiError:
        logger.warning("failed to register Telegram webhook at %s", url, exc_info=True)
    else:
        logger.info("registered Telegram webhook at %s", url)
