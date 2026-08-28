"""Telegram bot connection + test send — see docs/api.md and docs/notifications.md."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user_id, get_notification_repository
from app.config.settings import get_settings
from app.domain.matching.models import JobMatch, Recommendation, ScoreBreakdown
from app.domain.notifications.models import JobMatchNotification
from app.integrations.notifications.factory import build_telegram_provider
from app.integrations.notifications.telegram_provider import (
    TelegramApiError,
    TelegramNotificationProvider,
)
from app.repositories.notification_repository import NotificationRepository

router = APIRouter(prefix="/api/integrations/telegram", tags=["telegram"])


class ConnectTelegramRequest(BaseModel):
    bot_token: str
    chat_id: str


class ConnectTelegramResponse(BaseModel):
    status: str
    bot_username: str | None


def _sample_notification() -> JobMatchNotification:
    match = JobMatch(
        id="test",
        user_id="test",
        canonical_job_id="test",
        eligible=True,
        requirement_match=87.0,
        practical_fit=87.0,
        breakdown=ScoreBreakdown(90, 90, 90, 90, 100, 100, 80, 100),
        recommendation=Recommendation.APPLY,
    )
    return JobMatchNotification(
        match=match,
        job_title="Test notification",
        company="Job Intelligence Platform",
        job_url="https://github.com",
    )


@router.post("/connect", response_model=ConnectTelegramResponse)
async def connect_telegram(
    payload: ConnectTelegramRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    repository: NotificationRepository = Depends(get_notification_repository),
) -> ConnectTelegramResponse:
    provider = TelegramNotificationProvider(payload.bot_token, payload.chat_id)
    try:
        bot_info = await provider.verify()
    except TelegramApiError as exc:
        raise HTTPException(status_code=422, detail=f"invalid bot token: {exc}") from exc

    await repository.save_telegram_integration(user_id, payload.bot_token, payload.chat_id)
    return ConnectTelegramResponse(status="connected", bot_username=bot_info.get("username"))


@router.post("/test")
async def test_telegram(
    user_id: uuid.UUID = Depends(get_current_user_id),
    repository: NotificationRepository = Depends(get_notification_repository),
) -> dict[str, str]:
    provider = await build_telegram_provider(user_id, repository, get_settings())
    if provider is None:
        raise HTTPException(
            status_code=422, detail="no Telegram bot connected — POST /connect first"
        )

    try:
        await provider.send_job_match(_sample_notification())
    except TelegramApiError as exc:
        raise HTTPException(status_code=502, detail=f"send failed: {exc}") from exc

    return {"status": "sent"}
