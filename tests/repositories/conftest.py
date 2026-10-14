"""Fixtures: in-memory SQLite stands in for the central DB (no external server needed)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.models import Role


async def make_engine() -> AsyncEngine:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


@pytest.fixture
async def central_session() -> AsyncIterator[AsyncSession]:
    engine = await make_engine()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def seed_roles(session: AsyncSession) -> None:
    await session.execute(
        insert(Role),
        [{"name": "admin"}, {"name": "officer"}, {"name": "viewer"}],
    )
    await session.commit()
