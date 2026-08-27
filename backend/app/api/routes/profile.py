from fastapi import APIRouter

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
async def get_profile() -> None:
    raise NotImplementedError


@router.patch("")
async def update_profile() -> None:
    raise NotImplementedError
