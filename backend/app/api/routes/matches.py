from fastapi import APIRouter

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("")
async def list_matches() -> None:
    raise NotImplementedError
