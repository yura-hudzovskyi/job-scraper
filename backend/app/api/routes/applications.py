from fastapi import APIRouter

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("")
async def list_applications() -> None:
    raise NotImplementedError
