"""Health checks (agent.md §7.9, E29). No HTTP concerns."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import ping_engine
from app.domain.static_config import load_static_config


async def central_db_ready(engine: AsyncEngine) -> bool:
    """Readiness: the central database answers ``SELECT 1``."""
    return await ping_engine(engine)


def static_config_ready(path: str) -> bool:
    """Readiness: the static plant configuration loads and validates."""
    try:
        load_static_config.cache_clear()
        load_static_config(path)
    except (OSError, ValueError):
        return False
    return True
