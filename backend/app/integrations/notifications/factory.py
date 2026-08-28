"""Builds a TelegramNotificationProvider for a user: DB-stored credentials (saved via
POST /api/integrations/telegram/connect) take priority over the Settings env-var
fallback, so the tool is usable either way.
"""

import uuid

from app.config.settings import Settings
from app.integrations.notifications.telegram_provider import TelegramNotificationProvider
from app.repositories.notification_repository import NotificationRepository


async def build_telegram_provider(
    user_id: uuid.UUID, repository: NotificationRepository, settings: Settings
) -> TelegramNotificationProvider | None:
    stored = await repository.get_telegram_integration(user_id)
    if stored is not None:
        bot_token, chat_id = stored
        return TelegramNotificationProvider(bot_token, chat_id)

    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotificationProvider(settings.telegram_bot_token, settings.telegram_chat_id)

    return None
