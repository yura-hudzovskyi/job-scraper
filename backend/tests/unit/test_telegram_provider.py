"""Needs network access. No real bot token required — verify() is checked against
Telegram's real (and free) auth-rejection path, not actual message delivery.
"""

import pytest

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
    _action_buttons,
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
        gaps=[MatchGap(label="aws", critical=False)],
        recommendation=Recommendation.APPLY,
    )


def _notification() -> JobMatchNotification:
    return JobMatchNotification(
        match=_match(),
        job_title="Senior Full Stack Engineer",
        company="Acme",
        job_url="https://example.com/jobs/1",
    )


def test_format_message_includes_key_fields() -> None:
    text = _format_message(_notification())
    assert "84% MATCH" in text
    assert "Senior Full Stack Engineer" in text
    assert "Acme" in text
    assert "react" in text
    assert "aws" in text
    assert "https://example.com/jobs/1" in text


def test_action_buttons_carry_canonical_job_id() -> None:
    buttons = _action_buttons("c1")
    callback_data = [button["callback_data"] for row in buttons for button in row]
    assert callback_data == ["save:c1", "applied:c1", "reject:c1"]


@pytest.mark.asyncio
async def test_bogus_bot_token_reaches_real_api_and_is_rejected() -> None:
    provider = TelegramNotificationProvider("123456:fake-bot-token-for-smoke-test", "12345")

    with pytest.raises(TelegramApiError):
        await provider.verify()
