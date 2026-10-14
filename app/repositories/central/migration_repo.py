"""SQL for the legacy -> central data copy (agent.md §5.9 / E30 ``migrate-central-data``).

``LegacySourceRepository`` reads the old MySQL schema (tolerating missing optional columns);
``TargetRepository`` upserts into the new central schema by primary key so reruns are idempotent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, func, insert, inspect, select, update
from sqlalchemy import table as core_table
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import column

from app.models import Role


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


class LegacySourceRepository:
    """Read-only access to the legacy (CodeIgniter / myth-auth) database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch(
        self, table_name: str, columns: Sequence[str], order_by: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all rows of ``table_name``; columns absent in the source come back as ``None``.

        ``table_name`` and ``columns`` are program constants, never user input. Returns an empty
        list when the source table does not exist.
        """
        conn = await self._session.connection()

        def _present(sync_conn: Connection) -> set[str]:
            insp = inspect(sync_conn)
            if not insp.has_table(table_name):
                return set()
            return {c["name"] for c in insp.get_columns(table_name)}

        present = await conn.run_sync(_present)
        selected = [c for c in columns if c in present]
        if not selected:
            return []
        tbl = core_table(table_name, *(column(c) for c in selected))
        stmt = select(*tbl.c)
        if order_by and order_by in selected:
            stmt = stmt.order_by(tbl.c[order_by])
        result = await self._session.execute(stmt)
        rows = [dict(r._mapping) for r in result]
        return [{c: row.get(c) for c in columns} for row in rows]


class TargetRepository:
    """Idempotent writes into the new central schema."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, model: type, rows: Sequence[Mapping[str, Any]], key: Sequence[str]
    ) -> UpsertCounts:
        """Insert missing rows and update changed ones, matching on ``key`` columns."""
        if not rows:
            return UpsertCounts()
        tbl: Table = model.__table__  # type: ignore[attr-defined]
        existing_rows = (await self._session.execute(select(tbl))).mappings().all()
        existing = {tuple(r[k] for k in key): dict(r) for r in existing_rows}

        to_insert: list[dict[str, Any]] = []
        updated = unchanged = 0
        for row in rows:
            ident = tuple(row[k] for k in key)
            current = existing.get(ident)
            if current is None:
                to_insert.append(dict(row))
                continue
            changes = {c: v for c, v in row.items() if c not in key and current.get(c) != v}
            if not changes:
                unchanged += 1
                continue
            cond = [tbl.c[k] == row[k] for k in key]
            await self._session.execute(update(tbl).where(*cond).values(**changes))
            updated += 1
        if to_insert:
            await self._session.execute(insert(tbl), to_insert)
        return UpsertCounts(len(to_insert), updated, unchanged)

    async def role_ids(self) -> dict[str, int]:
        """``roles.name -> roles.id``."""
        result = await self._session.execute(select(Role.name, Role.id))
        return {name: rid for name, rid in result}

    async def reset_sequences(self, models: Sequence[type]) -> None:
        """PostgreSQL only: move each ``id`` sequence past the explicitly inserted ids."""
        bind = self._session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        for model in models:
            tbl: Table = model.__table__  # type: ignore[attr-defined]
            max_id = select(func.coalesce(func.max(tbl.c.id), 0) + 1).scalar_subquery()
            seq = func.pg_get_serial_sequence(tbl.name, "id")
            await self._session.execute(select(func.setval(seq, max_id, False)))
