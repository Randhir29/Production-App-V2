"""Clean typed API mounted at ``API_V1_PREFIX`` (agent.md §6)."""

from fastapi import APIRouter

from app.api.v1 import health, meta, plants, scales, tables

router = APIRouter()
router.include_router(health.router)
router.include_router(plants.router)
router.include_router(meta.router)
router.include_router(scales.router)
router.include_router(tables.router)

__all__ = ["router"]
