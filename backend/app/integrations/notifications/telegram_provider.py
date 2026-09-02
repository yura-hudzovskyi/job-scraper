"""Telegram Bot API notification provider — the first delivery channel.

A swipe-card UX, not a long report: each match is a short card (score, title,
company, a couple of quick-read facts) with two buttons — Approve / Reject — the
same shape as a dating app's yes/no, not a document to study. Uses httpx directly:
the Bot API is a simple, stable REST API with no official Python SDK to
standardize on (same reasoning as every other HTTP integration here). See
docs/notifications.md.

Button taps arrive as callback_query updates via a Telegram webhook — see
integrations/notifications/telegram_webhook.py (registration, at app startup) and
api/routes/telegram.py's POST /webhook (the receiving endpoint), which calls
answer_callback_query/clear_reply_markup below to close the loop. See
docs/notifications.md for the webhook vs. polling tradeoff.
"""

import html
from typing import Any

import httpx

from app.domain.notifications.models import JobMatchNotification

_API_BASE = "https://api.telegram.org"

_SOURCE_LABELS = {"dou": "DOU", "djinni": "Djinni"}



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
        text = _format_message(notification)
        response = await self._client.post(
            "/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {
                    "inline_keyboard": _decision_buttons(notification.match.canonical_job_id)
                },
            },
        )
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))

    # --- Bot-wide operations, used by webhook registration (telegram_webhook.py)
    # and the webhook route (api/routes/telegram.py). Constructed the same way
    # telegram_bot_info's route already does for verify() — chat_id is irrelevant
    # to these, only the bot token matters.

    async def set_webhook(self, url: str, secret_token: str) -> None:
        """Idempotent — safe to call on every app startup even if already
        registered at the same URL. allowed_updates narrows delivery to button
        taps; secret_token is echoed back on every call in the
        X-Telegram-Bot-Api-Secret-Token header so the webhook route can verify a
        request actually came from Telegram."""
        response = await self._client.post(
            "/setWebhook",
            json={
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["callback_query"],
            },
        )
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        """Must be called for every callback_query received — otherwise the tapped
        button shows a loading spinner until Telegram times it out client-side."""
        response = await self._client.post(
            "/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
        )
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))

    async def clear_reply_markup(self, chat_id: int, message_id: int) -> None:
        """Removes the Approve/Reject buttons once a decision on this message has
        been recorded — leaves the card's text untouched, the cleared keyboard
        itself is the confirmation."""
        response = await self._client.post(
            "/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {}},
        )
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))


def _decision_buttons(canonical_job_id: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "✅ Approve", "callback_data": f"match:approve:{canonical_job_id}"},
            {"text": "❌ Reject", "callback_data": f"match:reject:{canonical_job_id}"},
        ]
    ]


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source.capitalize())


def _source_links_line(source_links: list[tuple[str, str]]) -> str:
    links = [
        f'<a href="{html.escape(url, quote=True)}">{html.escape(_source_label(source))}</a>'
        for source, url in source_links
    ]
    return "🔗 " + " · ".join(links)


def _salary_text(notification: JobMatchNotification) -> str | None:
    salary = notification.salary
    if salary is None or (salary.min is None and salary.max is None):
        return None
    if salary.min is not None and salary.max is not None:
        amount = f"{salary.min:g}–{salary.max:g}"
    else:
        amount = f"{salary.min if salary.min is not None else salary.max:g}"
    currency = f" {salary.currency}" if salary.currency else ""
    return f"💰 {amount}{currency}"


def _facts_line(notification: JobMatchNotification) -> str | None:
    """One compact line of quick-read facts — salary, remote, seniority. A swipe
    decision needs a glance, not a report."""
    facts = [
        _salary_text(notification),
        "📍 Remote" if notification.remote else None,
        f"🎓 {html.escape(notification.seniority)}" if notification.seniority else None,
    ]
    present = [fact for fact in facts if fact is not None]
    return " · ".join(present) if present else None


def _signals_line(notification: JobMatchNotification) -> str:
    """The two numbers behind the score, spelled out. A card that only shows a
    percentage is asking to be trusted; this one shows its working."""
    match = notification.match
    similarity = f"similarity {match.similarity * 100:.0f}%"
    if match.relevance is None:
        return f"🧮 {similarity} · not reranked"
    return f"🧮 {similarity} · rerank {match.relevance * 100:.0f}%"


def _stats_line(notification: JobMatchNotification) -> str:
    return (
        f"📊 {notification.pending_count} pending · "
        f"{notification.approved_count} approved · "
        f"{notification.rejected_count} rejected"
    )


def _format_message(notification: JobMatchNotification) -> str:
    """A short swipe card, not a report: score, title, one fact line, the signals
    the score came from, source links, and where the user stands overall."""
    match = notification.match
    lines = [
        f"<b>{match.score:.0f}% MATCH</b> · {match.recommendation.value.upper()}",
        "",
        f"<b>{html.escape(notification.job_title)}</b> — {html.escape(notification.company)}",
    ]

    facts_line = _facts_line(notification)
    if facts_line:
        lines.append(facts_line)
    lines += ["", _signals_line(notification)]
    lines += ["", _source_links_line(notification.source_links), "", _stats_line(notification)]
    return "\n".join(lines)
