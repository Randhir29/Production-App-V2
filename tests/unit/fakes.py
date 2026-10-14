"""In-memory stand-ins for plant sessions and PlantDatabases (no I/O)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self._rows)


class FakePlantSession:
    """Routes statements by text: probe, pg_proc lookup, CREATE FUNCTION."""

    def __init__(
        self,
        probe_rows: list[tuple[Any, ...]] | None = None,
        functions: set[str] | None = None,
        raise_on_execute: Exception | None = None,
        create_error: Exception | None = None,
    ) -> None:
        self.probe_rows = [(1,)] if probe_rows is None else probe_rows
        self.functions = set() if functions is None else functions
        self.raise_on_execute = raise_on_execute
        self.create_error = create_error
        self.statements: list[str] = []
        self.committed = False

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        text = str(stmt)
        self.statements.append(text)
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        if "production_log" in text:
            return FakeResult(self.probe_rows)
        if "pg_proc" in text:
            return FakeResult([(n,) for n in sorted(self.functions)])
        if "CREATE OR REPLACE FUNCTION" in text:
            if self.create_error is not None:
                raise self.create_error
            self.functions.update({"getgapbetweentimes", "getendgapforphase"})
            return FakeResult([])
        raise AssertionError(f"unexpected SQL: {text}")

    async def commit(self) -> None:
        self.committed = True


class FakePlantDatabases:
    """Mimics PlantDatabases: records targets, hands out one shared FakePlantSession."""

    def __init__(self, session: FakePlantSession, connect_error: Exception | None = None) -> None:
        self.shared = session
        self.connect_error = connect_error
        self.ro_targets: list[dict[str, Any]] = []
        self.rw_targets: list[dict[str, Any]] = []
        self._boot: set[tuple[Any, ...]] = set()

    @asynccontextmanager
    async def session(self, **target: Any) -> AsyncIterator[FakePlantSession]:
        self.ro_targets.append(target)
        if self.connect_error is not None:
            raise self.connect_error
        yield self.shared

    @asynccontextmanager
    async def rw_session(self, **target: Any) -> AsyncIterator[FakePlantSession]:
        self.rw_targets.append(target)
        yield self.shared

    def is_bootstrapped(self, *key: Any) -> bool:
        return key in self._boot

    def mark_bootstrapped(self, *key: Any) -> None:
        self._boot.add(key)
