"""Health checks (agent.md §7.9, E29). No HTTP concerns."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import ping_engine


async def central_db_ready(engine: AsyncEngine) -> bool:
    """Readiness: the central database answers ``SELECT 1``."""
    return await ping_engine(engine)
