"""Telegram Bot API notification provider — the first delivery channel.

Sends job match summaries via sendMessage, with inline action buttons (save/applied/
not relevant). Uses httpx directly: the Bot API is a simple, stable REST API with no
official Python SDK to standardize on (same reasoning as the Ollama provider).
See docs/notifications.md.
"""

from typing import Any

import httpx

from app.domain.notifications.models import JobMatchNotification

_API_BASE = "https://api.telegram.org"


class TelegramApiError(RuntimeError):
    pass


class TelegramNotificationProvider:
    def __init__(self, bot_token: str, chat_id: str):
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(base_url=f"{_API_BASE}/bot{bot_token}", timeout=15.0)

    async def verify(self) -> dict[str, Any]:
        """Calls getMe to confirm the token is valid. Raises TelegramApiError if not —
        used by the /connect and /test endpoints."""
        response = await self._client.get("/getMe")
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))
        result: dict[str, Any] = payload["result"]
        return result

    async def send_job_match(self, notification: JobMatchNotification) -> None:
        match = notification.match
        text = _format_message(notification)
        response = await self._client.post(
            "/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": _action_buttons(match.canonical_job_id)},
            },
        )
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))


def _format_message(notification: JobMatchNotification) -> str:
    match = notification.match
    lines = [
        f"<b>{match.practical_fit:.0f}% MATCH</b>",
        "",
        f"<b>{notification.job_title}</b> — {notification.company}",
        "",
    ]
    if match.strengths:
        lines.append("✅ " + ", ".join(reason.label for reason in match.strengths))
    if match.gaps:
        lines.append("⚠️ " + ", ".join(gap.label for gap in match.gaps))
    lines += [
        "",
        (
            f"Requirement match: {match.requirement_match:.0f}%   "
            f"Practical fit: {match.practical_fit:.0f}%"
        ),
        "",
        notification.job_url,
    ]
    return "\n".join(lines)


def _action_buttons(canonical_job_id: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "⭐ Save", "callback_data": f"save:{canonical_job_id}"},
            {"text": "✅ Applied", "callback_data": f"applied:{canonical_job_id}"},
            {"text": "🚫 Not relevant", "callback_data": f"reject:{canonical_job_id}"},
        ]
    ]
