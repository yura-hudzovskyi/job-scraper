from fastapi import APIRouter

router = APIRouter(prefix="/api/cv", tags=["cv"])


@router.post("")
async def upload_cv() -> None:
    raise NotImplementedError


@router.get("")
async def list_cvs() -> None:
    raise NotImplementedError


@router.post("/analyze")
async def analyze_cv() -> None:
    raise NotImplementedError
