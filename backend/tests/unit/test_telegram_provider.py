"""Needs network access. No real bot token required — verify() is checked against
Telegram's real (and free) auth-rejection path, not actual message delivery.
"""

import pytest

from app.domain.jobs.models import SalaryRange
from app.domain.matching.models import (
    JobMatch,
    MatchGap,
    MatchReason,
    Recommendation,
    ScoreBreakdown,
)
from app.domain.notifications.models import JobMatchNotification
from app.integrations.notifications.telegram_provider import (
    TelegramApiError,
    TelegramNotificationProvider,
    _decision_buttons,
    _format_message,
)


def _match() -> JobMatch:
    return JobMatch(
        id="m1",
        user_id="u1",
        canonical_job_id="c1",
        eligible=True,
        requirement_match=70.0,
        practical_fit=84.0,
        breakdown=ScoreBreakdown(86, 91, 75, 88, 100, 100, 70, 100),
        strengths=[MatchReason(label="react", detail="react appears in the job and profile")],
        gaps=[MatchGap(label="aws", critical=True)],
        recommendation=Recommendation.APPLY,
    )


def _notification(**overrides: object) -> JobMatchNotification:
    defaults: dict[str, object] = {
        "match": _match(),
        "job_title": "Senior Full Stack Engineer",
        "company": "Acme",
        "source_links": [("dou", "https://dou.ua/jobs/1"), ("djinni", "https://djinni.co/jobs/1")],
        "salary": SalaryRange(min=4000, max=5500, currency="USD"),
        "seniority": "Senior",
        "required_experience_years": 3.0,
        "remote": True,
        "pending_count": 12,
        "approved_count": 5,
        "rejected_count": 3,
    }
    defaults.update(overrides)
    return JobMatchNotification(**defaults)  # type: ignore[arg-type]


def test_format_message_includes_key_fields() -> None:
    text = _format_message(_notification())
    assert "84% MATCH" in text
    assert "APPLY" in text
    assert "Senior Full Stack Engineer" in text
    assert "Acme" in text
    assert "react" in text
    assert "aws (required)" in text
    assert "4000" in text and "5500" in text and "USD" in text
    assert "Senior" in text
    assert "Remote" in text


def test_format_message_is_short_and_shows_running_decision_counts() -> None:
    # This is a swipe card, not a report — no separate "requirement match" line,
    # and the running Approve/Reject totals must be visible on every card.
    text = _format_message(_notification())
    assert "Requirement match" not in text
    assert "12 pending" in text
    assert "5 approved" in text
    assert "3 rejected" in text


def test_decision_buttons_carry_the_canonical_job_id() -> None:
    buttons = _decision_buttons("c1")
    callback_data = [button["callback_data"] for row in buttons for button in row]
    assert callback_data == ["match:approve:c1", "match:reject:c1"]


def test_format_message_links_every_source_by_name() -> None:
    text = _format_message(_notification())
    assert '<a href="https://dou.ua/jobs/1">DOU</a>' in text
    assert '<a href="https://djinni.co/jobs/1">Djinni</a>' in text


def test_format_message_escapes_html_in_scraped_text() -> None:
    text = _format_message(_notification(job_title="C++ & <Backend> Dev", company="A & B Corp"))
    assert "<Backend>" not in text
    assert "C++ &amp;" in text
    assert "A &amp; B Corp" in text


@pytest.mark.asyncio
async def test_bogus_bot_token_reaches_real_api_and_is_rejected() -> None:
    provider = TelegramNotificationProvider("123456:fake-bot-token-for-smoke-test", "12345")

    with pytest.raises(TelegramApiError):
        await provider.verify()


@pytest.mark.asyncio
async def test_get_updates_with_a_bogus_token_is_rejected() -> None:
    provider = TelegramNotificationProvider("123456:fake-bot-token-for-smoke-test", "unused")

    with pytest.raises(TelegramApiError):
        await provider.get_updates(offset=None)


@pytest.mark.asyncio
async def test_answer_callback_query_with_a_bogus_token_is_rejected() -> None:
    provider = TelegramNotificationProvider("123456:fake-bot-token-for-smoke-test", "unused")

    with pytest.raises(TelegramApiError):
        await provider.answer_callback_query("fake-callback-id", "Approved!")


@pytest.mark.asyncio
async def test_clear_reply_markup_with_a_bogus_token_is_rejected() -> None:
    provider = TelegramNotificationProvider("123456:fake-bot-token-for-smoke-test", "unused")

    with pytest.raises(TelegramApiError):
        await provider.clear_reply_markup(12345, 1)
