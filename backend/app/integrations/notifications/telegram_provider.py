"""Telegram Bot API notification provider — the first delivery channel.

Sends job match summaries via sendMessage, with inline action buttons (save/apply/
hide/not relevant). See docs/notifications.md.
"""

from app.domain.matching.models import JobMatch


class TelegramNotificationProvider:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def send_job_match(self, match: JobMatch) -> None:
        raise NotImplementedError
