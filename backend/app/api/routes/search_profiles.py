from fastapi import APIRouter

router = APIRouter(prefix="/api/search-profiles", tags=["search-profiles"])


@router.get("")
async def list_search_profiles() -> None:
    raise NotImplementedError


@router.post("")
async def create_search_profile() -> None:
    raise NotImplementedError


@router.patch("/{search_profile_id}")
async def update_search_profile(search_profile_id: str) -> None:
    raise NotImplementedError
