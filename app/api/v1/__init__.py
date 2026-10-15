"""Clean typed API mounted at ``API_V1_PREFIX`` (agent.md §6)."""

from fastapi import APIRouter

from app.api.deps import ReadAccess
from app.api.v1 import auth, health, meta, plants, reports, scales, tables

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
for guarded in (plants, meta, scales, tables):
    router.include_router(guarded.router, dependencies=[ReadAccess])
router.include_router(reports.router)  # per-route guards: E27 also accepts an API key

__all__ = ["router"]
