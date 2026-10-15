"""Database engines (agent.md §7.4).

* Central DB: one pooled ``AsyncEngine`` built from ``CENTRAL_DATABASE_URL``.
* Plant DBs: read-only PostgreSQL, one pooled engine per (host, port, dbname, user), kept in a
  bounded LRU + TTL cache and guarded by a per-plant concurrency semaphore. Every connection
  acquisition is logged with its latency. Credentials are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy import event, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings

logger = logging.getLogger("prodash.core.db")

SQL_PING = text("SELECT 1")

PlantKey = tuple[str, int, str, str]


def configure_event_loop_policy() -> None:
    """Use the selector event loop on Windows, which psycopg's async driver requires.

    Call before the loop is created (scripts, pytest). Under uvicorn the loop already exists
    when the app is imported, so start it with ``--loop app.core.db:selector_loop_factory``.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Loop factory for ``uvicorn --loop``: a selector loop on every platform."""
    return asyncio.SelectorEventLoop()


def attach_query_timing(engine: Engine, name: str, slow_ms: int) -> None:
    """Log every statement's duration (DEBUG) and a WARNING when it exceeds ``slow_ms``.

    Only the first 80 characters of the statement text are logged, whitespace collapsed; bind
    parameters are never logged (agent.md §7.7).
    """

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        conn.info.setdefault("_query_started", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        stack = conn.info.get("_query_started")
        if not stack:
            return
        elapsed_ms = (time.perf_counter() - stack.pop()) * 1000
        label = " ".join(str(statement).split())[:80]
        if elapsed_ms >= slow_ms:
            logger.warning("slow query engine=%s duration_ms=%.1f statement=%s", name, elapsed_ms, label)
        else:
            logger.debug("query engine=%s duration_ms=%.1f statement=%s", name, elapsed_ms, label)


def create_central_engine(settings: Settings) -> AsyncEngine:
    """Build the pooled async engine for the central DB (MySQL or PostgreSQL)."""
    engine = create_async_engine(
        settings.central_database_url.get_secret_value(),
        pool_size=settings.central_db_pool_size,
        max_overflow=settings.central_db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": settings.central_db_connect_timeout_s},
    )
    attach_query_timing(engine.sync_engine, "central", settings.slow_query_ms)
    return engine


def create_central_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory for per-request central-DB sessions."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping_engine(engine: AsyncEngine) -> bool:
    """Return True when ``SELECT 1`` succeeds on the engine; never raises."""
    try:
        async with engine.connect() as conn:
            await conn.execute(SQL_PING)
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("central database ping failed error=%s", type(exc).__name__)
        return False
    return True


@dataclass(slots=True)
class _PlantEntry:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    semaphore: asyncio.Semaphore
    created_at: float = field(default_factory=time.monotonic)


class PlantDatabases:
    """Per-plant read-only PostgreSQL access with engine caching.

    Usage::

        async with plant_dbs.session(host=h, port=p, dbname=d, user=u, password=pw) as session:
            await session.execute(...)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._entries: OrderedDict[PlantKey, _PlantEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._bootstrapped: set[PlantKey] = set()

    @staticmethod
    def build_url(host: str, port: int, dbname: str, user: str, password: str) -> URL:
        """Build the psycopg async URL; ``URL.create`` handles quoting of special characters."""
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=host,
            port=port,
            database=dbname,
        )

    def _create_entry(self, url: URL) -> _PlantEntry:
        s = self._settings
        engine = create_async_engine(
            url,
            pool_size=s.plant_db_pool_size,
            max_overflow=s.plant_db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": s.plant_db_connect_timeout_s,
                "options": (
                    f"-c statement_timeout={int(s.plant_db_statement_timeout_ms)} "
                    "-c default_transaction_read_only=on"
                ),
            },
        )
        attach_query_timing(engine.sync_engine, "plant", s.slow_query_ms)
        return _PlantEntry(
            engine=engine,
            sessions=async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
            semaphore=asyncio.Semaphore(s.plant_max_concurrency),
        )

    async def _acquire_entry(self, key: PlantKey, password: str, ttl_s: float) -> _PlantEntry:
        stale: list[AsyncEngine] = []
        async with self._lock:
            now = time.monotonic()
            for k, e in list(self._entries.items()):
                if now - e.created_at > ttl_s:
                    stale.append(self._entries.pop(k).engine)
            entry = self._entries.get(key)
            if entry is None:
                host, port, dbname, user = key
                entry = self._create_entry(self.build_url(host, port, dbname, user, password))
                self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._settings.plant_engine_cache_max:
                _, evicted = self._entries.popitem(last=False)
                stale.append(evicted.engine)
        for engine in stale:
            await engine.dispose()
        return entry

    @asynccontextmanager
    async def session(
        self,
        *,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
    ) -> AsyncIterator[AsyncSession]:
        """Yield a read-only session on the given plant database.

        The connection is established (or checked out of the pool) before yielding, and the
        attempt is logged with its latency in milliseconds. Connection errors propagate
        unchanged after being logged.
        """
        key: PlantKey = (host, port, dbname, user)
        entry = await self._acquire_entry(key, password, self._settings.plant_engine_ttl_s)
        async with entry.semaphore:
            session = entry.sessions()
            started = time.perf_counter()
            try:
                await session.connection()
            except Exception as exc:
                logger.warning(
                    "plant db connection failed host=%s port=%s db=%s latency_ms=%.1f error=%s",
                    host,
                    port,
                    dbname,
                    (time.perf_counter() - started) * 1000,
                    type(exc).__name__,
                )
                await session.close()
                raise
            logger.info(
                "plant db connection ok host=%s port=%s db=%s latency_ms=%.1f",
                host,
                port,
                dbname,
                (time.perf_counter() - started) * 1000,
            )
            try:
                yield session
            finally:
                await session.close()

    @asynccontextmanager
    async def rw_session(
        self,
        *,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
    ) -> AsyncIterator[AsyncSession]:
        """Yield a short-lived read-write session (unpooled), used only for the PL/pgSQL bootstrap.

        Plant data stays read-only: regular sessions come from ``session()`` (§7.4, §8.4.6).
        """
        s = self._settings
        engine = create_async_engine(
            self.build_url(host, port, dbname, user, password),
            poolclass=NullPool,
            connect_args={
                "connect_timeout": s.plant_db_connect_timeout_s,
                "options": f"-c statement_timeout={int(s.plant_db_statement_timeout_ms)}",
            },
        )
        started = time.perf_counter()
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await session.connection()
                logger.info(
                    "plant db rw connection ok host=%s port=%s db=%s latency_ms=%.1f",
                    host, port, dbname, (time.perf_counter() - started) * 1000,
                )  # fmt: skip
                yield session
        finally:
            await engine.dispose()

    def is_bootstrapped(self, host: str, port: int, dbname: str, user: str) -> bool:
        """Whether the gap functions were already verified/created for this plant (D-21)."""
        return (host, port, dbname, user) in self._bootstrapped

    def mark_bootstrapped(self, host: str, port: int, dbname: str, user: str) -> None:
        self._bootstrapped.add((host, port, dbname, user))

    async def invalidate(self, host: str, port: int, dbname: str, user: str) -> None:
        """Drop and dispose the cached engine for a plant (e.g. after its credentials change)."""
        self._bootstrapped.discard((host, port, dbname, user))
        async with self._lock:
            entry = self._entries.pop((host, port, dbname, user), None)
        if entry is not None:
            await entry.engine.dispose()

    async def dispose(self) -> None:
        """Dispose every cached plant engine (application shutdown)."""
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await entry.engine.dispose()
