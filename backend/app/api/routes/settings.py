from fastapi import APIRouter

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings_view() -> None:
    raise NotImplementedError


@router.patch("")
async def update_settings_view() -> None:
    raise NotImplementedError
