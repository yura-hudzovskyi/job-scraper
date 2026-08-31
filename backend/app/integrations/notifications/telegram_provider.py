"""Telegram Bot API notification provider — the first delivery channel.

Sends job match summaries via sendMessage. Uses httpx directly: the Bot API is a
simple, stable REST API with no official Python SDK to standardize on (same
reasoning as the Ollama provider). See docs/notifications.md.

No inline action buttons (save/applied/not relevant) — there's no callback-query
webhook handler wired up anywhere in this app yet, so they rendered but silently
did nothing on tap. Re-add them once something actually handles the callback.
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
            },
        )
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramApiError(payload.get("description", "unknown Telegram API error"))


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source.capitalize())


def _source_links_line(source_links: list[tuple[str, str]]) -> str:
    links = [
        f'<a href="{html.escape(url, quote=True)}">{html.escape(_source_label(source))}</a>'
        for source, url in source_links
    ]
    return "🔗 " + " · ".join(links)


def _salary_line(notification: JobMatchNotification) -> str | None:
    salary = notification.salary
    if salary is None or (salary.min is None and salary.max is None):
        return None
    if salary.min is not None and salary.max is not None:
        amount = f"{salary.min:g}–{salary.max:g}"
    else:
        amount = f"{salary.min if salary.min is not None else salary.max:g}"
    currency = f" {salary.currency}" if salary.currency else ""
    return f"💰 {amount}{currency}"


def _experience_line(notification: JobMatchNotification) -> str | None:
    parts: list[str] = []
    if notification.seniority:
        parts.append(html.escape(notification.seniority))
    if notification.required_experience_years:
        parts.append(f"{notification.required_experience_years:g}+ yrs required")
    return "🎓 " + " · ".join(parts) if parts else None


def _format_message(notification: JobMatchNotification) -> str:
    match = notification.match
    lines = [
        f"<b>{match.practical_fit:.0f}% MATCH</b>"
        + (f" · {match.recommendation.value.upper()}" if match.recommendation else ""),
        "",
        f"<b>{html.escape(notification.job_title)}</b> — {html.escape(notification.company)}",
        "",
    ]

    info_lines = [
        _salary_line(notification),
        "📍 Remote" if notification.remote else None,
        _experience_line(notification),
    ]
    lines += [line for line in info_lines if line is not None]
    lines.append("")

    if match.strengths:
        lines.append("✅ " + ", ".join(html.escape(reason.label) for reason in match.strengths))
    if match.gaps:
        gap_labels = ", ".join(
            html.escape(gap.label) + (" (required)" if gap.critical else "") for gap in match.gaps
        )
        lines.append("⚠️ " + gap_labels)

    lines += [
        "",
        (
            f"Requirement match: {match.requirement_match:.0f}%   "
            f"Practical fit: {match.practical_fit:.0f}%"
        ),
        "",
        _source_links_line(notification.source_links),
    ]
    return "\n".join(lines)
