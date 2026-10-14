"""Plant-DB probe and function bootstrap SQL (agent.md §5.8, §7.4). Read-only except bootstrap."""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.plant.sql.gap_functions import (
    GAP_FUNCTION_NAMES,
    SQL_CREATE_GET_END_GAP_FOR_PHASE,
    SQL_CREATE_GET_GAP_BETWEEN_TIMES,
)

SQL_CONNECTIVITY_PROBE = text("SELECT production_log_id FROM production_log LIMIT 1")
SQL_GAP_FUNCTIONS_EXIST = text("SELECT proname FROM pg_proc WHERE proname IN :names").bindparams(
    bindparam("names", expanding=True)
)


class ConnectivityRepository:
    """Queries run on a plant connection."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def probe(self) -> bool:
        """``SELECT production_log_id … LIMIT 1``: True when a row came back."""
        result = await self._session.execute(SQL_CONNECTIVITY_PROBE)
        return result.first() is not None

    async def gap_functions_exist(self) -> bool:
        """True when both V2 gap helper functions are present on the plant database."""
        result = await self._session.execute(SQL_GAP_FUNCTIONS_EXIST, {"names": list(GAP_FUNCTION_NAMES)})
        return {row[0] for row in result} >= set(GAP_FUNCTION_NAMES)

    async def create_gap_functions(self) -> None:
        """``CREATE OR REPLACE`` both helpers. Needs a read-write connection (D-21)."""
        await self._session.execute(SQL_CREATE_GET_GAP_BETWEEN_TIMES)
        await self._session.execute(SQL_CREATE_GET_END_GAP_FOR_PHASE)
        await self._session.commit()
