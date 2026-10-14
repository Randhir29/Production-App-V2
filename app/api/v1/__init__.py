"""Clean typed API mounted at ``API_V1_PREFIX`` (agent.md §6)."""

from fastapi import APIRouter

from app.api.v1 import health

router = APIRouter()
router.include_router(health.router)

__all__ = ["router"]
