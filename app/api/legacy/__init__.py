"""Legacy-compatible router mounted at ``LEGACY_PREFIX`` (agent.md §6).

Thin adapters over the same services as v1 are registered here as each endpoint is ported;
no business logic belongs in this package.
"""

from fastapi import APIRouter

router = APIRouter(tags=["legacy"])

__all__ = ["router"]
