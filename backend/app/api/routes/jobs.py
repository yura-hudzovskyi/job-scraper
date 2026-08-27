from fastapi import APIRouter

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs() -> None:
    raise NotImplementedError


@router.get("/{job_id}")
async def get_job(job_id: str) -> None:
    raise NotImplementedError


@router.get("/{job_id}/match")
async def get_job_match(job_id: str) -> None:
    raise NotImplementedError


@router.post("/{job_id}/rescore")
async def rescore_job(job_id: str) -> None:
    raise NotImplementedError


@router.post("/{job_id}/save")
async def save_job(job_id: str) -> None:
    raise NotImplementedError


@router.post("/{job_id}/apply")
async def apply_to_job(job_id: str) -> None:
    raise NotImplementedError


@router.post("/{job_id}/reject")
async def reject_job(job_id: str) -> None:
    raise NotImplementedError
