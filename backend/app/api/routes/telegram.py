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
    chat_id: str


class ConnectTelegramResponse(BaseModel):
    status: str
    bot_username: str | None


class TelegramStatusResponse(BaseModel):
    connected: bool


class TelegramBotInfoResponse(BaseModel):
    username: str | None


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
        source_links=[("dou", "https://github.com")],
        pending_count=1,
    )


@router.get("/status", response_model=TelegramStatusResponse)
async def telegram_status(
    user_id: uuid.UUID = Depends(get_current_user_id),
    repository: NotificationRepository = Depends(get_notification_repository),
) -> TelegramStatusResponse:
    integration = await repository.get_telegram_integration(user_id)
    return TelegramStatusResponse(connected=integration is not None)


@router.get("/bot-info", response_model=TelegramBotInfoResponse)
async def telegram_bot_info(
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> TelegramBotInfoResponse:
    """Public-ish identity of the one shared bot everyone connects to (see
    /connect) — lets the UI tell a user which bot to message before they've
    connected anything themselves."""
    bot_token = get_settings().telegram_bot_token
    if not bot_token:
        return TelegramBotInfoResponse(username=None)

    provider = TelegramNotificationProvider(bot_token, chat_id="unused")
    try:
        bot_info = await provider.verify()
    except TelegramApiError:
        return TelegramBotInfoResponse(username=None)
    return TelegramBotInfoResponse(username=bot_info.get("username"))


@router.post("/connect", response_model=ConnectTelegramResponse)
async def connect_telegram(
    payload: ConnectTelegramRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    repository: NotificationRepository = Depends(get_notification_repository),
) -> ConnectTelegramResponse:
    """Connects the current user's chat id to the one shared bot configured via
    TELEGRAM_BOT_TOKEN — there's no per-user bot token to collect, just the chat
    id, so nobody needs to be handed the bot's token out-of-band."""
    bot_token = get_settings().telegram_bot_token
    if not bot_token:
        raise HTTPException(status_code=503, detail="no Telegram bot configured on the server")

    provider = TelegramNotificationProvider(bot_token, payload.chat_id)
    try:
        bot_info = await provider.verify()
    except TelegramApiError as exc:
        raise HTTPException(status_code=422, detail=f"invalid bot token: {exc}") from exc

    await repository.save_telegram_integration(user_id, bot_token, payload.chat_id)
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
