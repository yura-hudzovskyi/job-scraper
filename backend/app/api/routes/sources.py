from fastapi import APIRouter

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("")
async def list_sources() -> None:
    """Per-source health: last success/failure, consecutive failures, parse errors."""
    raise NotImplementedError


@router.post("/{source_id}/sync")
async def sync_source(source_id: str) -> None:
    raise NotImplementedError
