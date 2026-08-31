"""Polls Telegram's getUpdates for Approve/Reject button taps on swipe cards (see
integrations/notifications/telegram_provider.py) and applies each one via
TelegramCallbackService — the mechanism this app uses instead of a public
webhook (see docs/notifications.md for why, and that provider's own docstring).

Runs on a short Celery Beat interval (see celery_app.py). The offset cursor lives
in Redis, not Postgres — it's pure polling-mechanics bookkeeping, not domain data,
same reasoning as DailyCallBudget's counters (app/integrations/ai/llm/budget.py).
"""

import asyncio
import logging

import redis.asyncio as redis

from app.config.settings import get_settings
from app.db.session import session_scope
from app.integrations.notifications.telegram_provider import TelegramNotificationProvider
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.telegram_callback_service import TelegramCallbackService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_OFFSET_KEY = "telegram:update_offset"


async def _run() -> int:
    settings = get_settings()
    bot_token = settings.telegram_bot_token
    if not bot_token:
        return 0

    redis_client = redis.from_url(settings.redis_url)
    bot_provider = TelegramNotificationProvider(bot_token, chat_id="unused")

    raw_offset = await redis_client.get(_OFFSET_KEY)
    offset = int(raw_offset) if raw_offset is not None else None
    updates = await bot_provider.get_updates(offset=offset)
    if not updates:
        return 0

    async with session_scope() as session:
        service = TelegramCallbackService(
            MatchRepository(session), NotificationRepository(session), bot_provider
        )
        for update in updates:
            try:
                await service.handle_update(update)
            except Exception:
                # One bad update must not get this batch stuck forever: if we
                # crashed before advancing the offset below, the next tick would
                # re-fetch and re-crash on the exact same update indefinitely.
                logger.warning(
                    "failed to process Telegram update %s — skipping it",
                    update.get("update_id"),
                    exc_info=True,
                )

    highest_update_id = max(update["update_id"] for update in updates)
    await redis_client.set(_OFFSET_KEY, highest_update_id + 1)
    return len(updates)


@celery_app.task(name="telegram.poll_updates")
def poll_telegram_updates() -> dict[str, int]:
    count = asyncio.run(_run())
    return {"updates_processed": count}
