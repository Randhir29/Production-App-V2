"""Resolve a plant's credentials and open read-only sessions (agent.md §7.4).

Maps driver errors to ``PlantUnreachable`` / ``PlantQueryError``; messages never carry the DSN.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import DataError, DBAPIError, InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import PlantDatabases
from app.core.errors import AppError, PlantNotFound, PlantQueryError, PlantUnreachable
from app.repositories.central.plant_repo import PlantRecord, PlantRepository
from app.repositories.plant.connectivity_repo import ConnectivityRepository

logger = logging.getLogger("prodash.services.plant_access")


class PlantFunctionsUnavailable(AppError):
    """The V2 gap PL/pgSQL functions are missing and could not be created."""

    http_status = 502
    code = "PLANT_FUNCTIONS_UNAVAILABLE"
    message = "Plant database helper functions are unavailable"

    def __init__(self, plant: str) -> None:
        super().__init__(plant=plant)


def _has_credentials(record: PlantRecord) -> bool:
    return bool(record.host_ip and record.db_database and record.db_user)


class PlantAccess:
    """Opens plant sessions by ``short_name`` using credentials from the central ``plants`` table."""

    def __init__(self, plants: PlantRepository, databases: PlantDatabases, settings: Settings) -> None:
        self._plants = plants
        self._databases = databases
        self._settings = settings

    async def resolve(self, plant: str) -> PlantRecord:
        """Return the plant row, or raise ``PlantNotFound`` (also when credentials are incomplete)."""
        record = await self._plants.get_by_short_name(plant)
        if record is None or not _has_credentials(record):
            raise PlantNotFound(plant)
        return record

    def _target(self, record: PlantRecord) -> dict[str, object]:
        return {
            "host": record.host_ip,
            "port": self._settings.plant_db_port,
            "dbname": record.db_database,
            "user": record.db_user,
            "password": record.db_password or "",
        }

    @asynccontextmanager
    async def session(self, plant: str) -> AsyncIterator[AsyncSession]:
        """Yield a read-only session on the plant DB.

        Raises ``PlantNotFound``, ``PlantUnreachable`` (connect/OS/timeout errors) or
        ``PlantQueryError`` (SQL errors raised inside the block).
        """
        record = await self.resolve(plant)
        try:
            async with self._databases.session(**self._target(record)) as session:  # type: ignore[arg-type]
                yield session
        except (OperationalError, InterfaceError, OSError, TimeoutError) as exc:
            logger.warning("plant unreachable plant=%s error=%s", plant, type(exc).__name__)
            raise PlantUnreachable(plant) from exc
        except (ProgrammingError, DataError) as exc:
            logger.warning("plant query failed plant=%s error=%s", plant, type(exc).__name__)
            raise PlantQueryError(plant) from exc

    async def ensure_gap_functions(self, plant: str) -> None:
        """Make sure the V2 gap helper functions exist; runs once per plant per process (D-21).

        A read-only check comes first. If they are missing they are created over a separate
        read-write connection; a permission failure is logged and tolerated only when the
        functions turn out to exist, otherwise ``PlantFunctionsUnavailable`` is raised.
        """
        record = await self.resolve(plant)
        target = self._target(record)
        key = (record.host_ip, self._settings.plant_db_port, record.db_database, record.db_user)
        if self._databases.is_bootstrapped(*key):  # type: ignore[arg-type]
            return

        async with self.session(plant) as ro:
            if await ConnectivityRepository(ro).gap_functions_exist():
                self._databases.mark_bootstrapped(*key)  # type: ignore[arg-type]
                return

        try:
            async with self._databases.rw_session(**target) as rw:  # type: ignore[arg-type]
                await ConnectivityRepository(rw).create_gap_functions()
        except DBAPIError as exc:
            logger.warning("gap function bootstrap failed plant=%s error=%s", plant, type(exc).__name__)
            async with self.session(plant) as ro:
                if not await ConnectivityRepository(ro).gap_functions_exist():
                    raise PlantFunctionsUnavailable(plant) from exc
        self._databases.mark_bootstrapped(*key)  # type: ignore[arg-type]
