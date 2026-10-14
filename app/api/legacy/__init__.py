"""Legacy-compatible router mounted at ``LEGACY_PREFIX`` (agent.md §6).

Thin adapters over the same services as v1; they only reshape output into the PHP JSON.
"""

from fastapi import APIRouter

from app.api.legacy import prodash_compat

router = APIRouter()
router.include_router(prodash_compat.router)

__all__ = ["router"]
