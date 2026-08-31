"""Use case: apply one Approve/Reject button tap from a Telegram swipe card (see
integrations/notifications/telegram_provider.py) to the underlying JobMatch.
Called per-update by the webhook route (api/routes/telegram.py's POST /webhook)
— kept as its own service, not inlined in the route, so it's testable with fakes
the same way every other use case in this app is.
"""

import logging
import uuid
from typing import Any

from app.domain.matching.models import MatchDecision
from app.integrations.notifications.telegram_provider import TelegramNotificationProvider
from app.repositories.match_repository import MatchRepository
from app.repositories.notification_repository import NotificationRepository

logger = logging.getLogger(__name__)

_ACTIONS = {"approve": MatchDecision.APPROVED, "reject": MatchDecision.REJECTED}
_ACTION_LABELS = {"approve": "Approved!", "reject": "Rejected."}


def _parse_callback_data(data: str) -> tuple[str, str] | None:
    """callback_data is "match:<approve|reject>:<canonical_job_id>" — see
    telegram_provider.py's _decision_buttons. Anything else (a stale format from a
    version this app no longer sends, a malformed payload) is not ours to handle."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "match" or parts[1] not in _ACTIONS:
        return None
    return parts[1], parts[2]


class TelegramCallbackService:
    def __init__(
        self,
        match_repository: MatchRepository,
        notification_repository: NotificationRepository,
        bot_provider: TelegramNotificationProvider,
    ):
        self._match_repository = match_repository
        self._notification_repository = notification_repository
        self._bot_provider = bot_provider

    async def handle_update(self, update: dict[str, Any]) -> None:
        """No-ops (rather than raising) for anything that isn't a recognized
        Approve/Reject tap — the webhook is registered with allowed_updates
        limited to callback_query, but a stray/malformed one should never fail
        the whole webhook request (see api/routes/telegram.py)."""
        callback_query = update.get("callback_query")
        if callback_query is None:
            return

        callback_query_id = callback_query.get("id")
        if callback_query_id is None:
            return  # can't even acknowledge it — nothing more we can do

        parsed = _parse_callback_data(callback_query.get("data", ""))
        if parsed is None:
            logger.warning("ignoring unrecognized Telegram callback: %r", callback_query.get("data"))
            await self._bot_provider.answer_callback_query(callback_query_id, "Unrecognized action.")
            return
        action, raw_canonical_job_id = parsed

        chat = callback_query.get("message", {}).get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            await self._bot_provider.answer_callback_query(callback_query_id, "Something went wrong.")
            return

        user_id = await self._notification_repository.get_user_id_for_chat_id(str(chat_id))
        if user_id is None:
            logger.warning("no user connected for Telegram chat_id %s", chat_id)
            await self._bot_provider.answer_callback_query(callback_query_id, "Not connected.")
            return

        try:
            canonical_job_id = uuid.UUID(raw_canonical_job_id)
        except ValueError:
            logger.warning("malformed canonical_job_id in callback_data: %r", raw_canonical_job_id)
            await self._bot_provider.answer_callback_query(callback_query_id, "Something went wrong.")
            return

        match = await self._match_repository.set_decision(user_id, canonical_job_id, _ACTIONS[action])
        if match is None:
            await self._bot_provider.answer_callback_query(callback_query_id, "Match not found.")
            return

        await self._bot_provider.answer_callback_query(callback_query_id, _ACTION_LABELS[action])

        message_id = callback_query.get("message", {}).get("message_id")
        if message_id is not None:
            await self._bot_provider.clear_reply_markup(chat_id, message_id)
