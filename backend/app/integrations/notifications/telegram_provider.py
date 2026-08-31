"""Telegram Bot API notification provider — the first delivery channel.

A swipe-card UX, not a long report: each match is a short card (score, title,
company, a couple of quick-read facts) with two buttons — Approve / Reject — the
same shape as a dating app's yes/no, not a document to study. Uses httpx directly:
the Bot API is a simple, stable REST API with no official Python SDK to
standardize on (same reasoning as the Ollama provider). See docs/notifications.md.

Button taps arrive as callback_query updates, which this class doesn't receive on
its own — see workers/tasks/telegram_poll.py, which polls getUpdates on a Celery
Beat schedule and calls answer_callback_query/clear_reply_markup below to close
the loop. Polling, not a webhook, even though this deployment does have a public
HTTPS domain (see docs/deployment.md) — see docs/notifications.md for why.
"""

import html
from typing import Any

import httpx

from app.domain.notifications.models import JobMatchNotification

_API_BASE = "https://api.telegram.org"

_SOURCE_LABELS = {"dou": "DOU", "djinni": "Djinni"}

_MAX_LISTED = 3


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

    # --- Bot-wide operations used by the update poller (workers/tasks/telegram_poll.py).
    # Constructed the same way telegram_bot_info's route already does for verify() —
    # chat_id is irrelevant to these, only the bot token matters.

    async def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict[str, Any]]:
        """timeout=0 (the default) is a quick, non-blocking poll — the caller is a
        Celery Beat tick, not a long-lived process, so a short poll re-run every
        few seconds is a better fit than tying up a worker slot in a long-poll."""
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["callback_query"]}
        if offset is not None:
            params["offset"] = offset
        response = await self._client.get("/getUpdates", params=params)
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))
        result: list[dict[str, Any]] = payload["result"]
        return result

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
    """One compact line of quick-read facts — salary, remote, seniority — instead
    of the old multi-line breakdown. A swipe decision needs a glance, not a report."""
    facts = [
        _salary_text(notification),
        "📍 Remote" if notification.remote else None,
        f"🎓 {html.escape(notification.seniority)}" if notification.seniority else None,
    ]
    present_facts = [fact for fact in facts if fact is not None]
    return " · ".join(present_facts) if present_facts else None


def _stats_line(notification: JobMatchNotification) -> str:
    return (
        f"📊 {notification.pending_count} pending · {notification.approved_count} approved · "
        f"{notification.rejected_count} rejected"
    )


def _format_message(notification: JobMatchNotification) -> str:
    """A short swipe card, not a report: score, title, one fact line, a couple of
    matched skills/gaps, source links, and where the user stands overall — enough
    to decide, nothing to read through. See the module docstring."""
    match = notification.match
    lines = [
        f"<b>{match.practical_fit:.0f}% MATCH</b>"
        + (f" · {match.recommendation.value.upper()}" if match.recommendation else ""),
        "",
        f"<b>{html.escape(notification.job_title)}</b> — {html.escape(notification.company)}",
    ]

    facts_line = _facts_line(notification)
    if facts_line:
        lines.append(facts_line)
    lines.append("")

    if match.strengths:
        labels = ", ".join(html.escape(reason.label) for reason in match.strengths[:_MAX_LISTED])
        lines.append(f"✅ {labels}")
    if match.gaps:
        labels = ", ".join(
            html.escape(gap.label) + (" (required)" if gap.critical else "")
            for gap in match.gaps[:_MAX_LISTED]
        )
        lines.append(f"⚠️ {labels}")

    lines += ["", _source_links_line(notification.source_links), "", _stats_line(notification)]
    return "\n".join(lines)
