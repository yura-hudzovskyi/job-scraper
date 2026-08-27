from fastapi import APIRouter

router = APIRouter(prefix="/api/integrations/telegram", tags=["telegram"])


@router.post("/connect")
async def connect_telegram() -> None:
    raise NotImplementedError


@router.post("/test")
async def test_telegram() -> None:
    raise NotImplementedError
