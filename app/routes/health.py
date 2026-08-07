from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Report that this API process can accept requests."""
    return {"status": "ok"}
