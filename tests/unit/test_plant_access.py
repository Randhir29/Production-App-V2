"""PlantAccess: credential resolution, error mapping, one-time gap-function bootstrap (§7.4, D-21)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core.config import Settings
from app.core.errors import PlantNotFound, PlantQueryError, PlantUnreachable
from app.repositories.central.plant_repo import PlantRecord
from app.services.plant_access import PlantAccess, PlantFunctionsUnavailable
from tests.unit.fakes import FakePlantDatabases, FakePlantSession

BOTH = {"getgapbetweentimes", "getendgapforphase"}
GOOD = PlantRecord(1, "p", "P", "Z", "10.0.0.9", "ro", "s3cret", "ccc")


class FakePlants:
    def __init__(self, record: PlantRecord | None) -> None:
        self._record = record

    async def get_by_short_name(self, name: str) -> PlantRecord | None:
        return self._record


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="k" * 40,
        plant_db_port=6543,
    )


def _access(dbs: FakePlantDatabases, record: PlantRecord | None = GOOD) -> PlantAccess:
    return PlantAccess(FakePlants(record), dbs, _settings())  # type: ignore[arg-type]


async def test_session_uses_central_credentials_and_configured_port() -> None:
    dbs = FakePlantDatabases(FakePlantSession())
    async with _access(dbs).session("p"):
        pass
    assert dbs.ro_targets == [
        {"host": "10.0.0.9", "port": 6543, "dbname": "ccc", "user": "ro", "password": "s3cret"}
    ]


async def test_unknown_or_incomplete_plant_is_not_found() -> None:
    dbs = FakePlantDatabases(FakePlantSession())
    with pytest.raises(PlantNotFound):
        async with _access(dbs, None).session("x"):
            pass
    incomplete = PlantRecord(1, "p", "P", "Z", None, "ro", "pw", "ccc")
    with pytest.raises(PlantNotFound):
        async with _access(dbs, incomplete).session("p"):
            pass


async def test_connect_error_maps_to_unreachable_without_dsn() -> None:
    err = OperationalError("x", {}, Exception("host 10.0.0.9 password s3cret"))
    dbs = FakePlantDatabases(FakePlantSession(), connect_error=err)
    with pytest.raises(PlantUnreachable) as exc:
        async with _access(dbs).session("p"):
            pass
    assert exc.value.http_status == 502 and exc.value.code == "PLANT_UNREACHABLE"
    assert "s3cret" not in exc.value.message and "10.0.0.9" not in str(exc.value.details)


async def test_timeout_and_os_errors_map_to_unreachable() -> None:
    for err in (TimeoutError(), ConnectionRefusedError()):
        dbs = FakePlantDatabases(FakePlantSession(), connect_error=err)
        with pytest.raises(PlantUnreachable):
            async with _access(dbs).session("p"):
                pass


async def test_sql_error_in_block_maps_to_query_error() -> None:
    dbs = FakePlantDatabases(FakePlantSession())
    with pytest.raises(PlantQueryError) as exc:
        async with _access(dbs).session("p"):
            raise ProgrammingError("SELECT", {}, Exception("relation missing"))
    assert exc.value.code == "PLANT_QUERY_FAILED"


async def test_bootstrap_skipped_when_functions_exist() -> None:
    sess = FakePlantSession(functions=set(BOTH))
    dbs = FakePlantDatabases(sess)
    await _access(dbs).ensure_gap_functions("p")
    assert dbs.rw_targets == []
    assert not any("CREATE" in s for s in sess.statements)


async def test_bootstrap_creates_functions_once() -> None:
    sess = FakePlantSession()
    dbs = FakePlantDatabases(sess)
    access = _access(dbs)
    await access.ensure_gap_functions("p")
    assert len(dbs.rw_targets) == 1 and sess.committed
    assert sum("CREATE OR REPLACE FUNCTION" in s for s in sess.statements) == 2
    before = len(sess.statements)
    await access.ensure_gap_functions("p")
    assert len(sess.statements) == before  # cached: no queries at all (D-21)


async def test_bootstrap_permission_denied_but_functions_exist_is_tolerated() -> None:
    err = ProgrammingError("CREATE", {}, Exception("permission denied"))
    sess = FakePlantSession(create_error=err)
    dbs = FakePlantDatabases(sess)
    original = sess.execute

    async def execute(stmt: Any, params: Any = None) -> Any:
        # a DBA created the functions between the first check and our failed CREATE
        if "CREATE OR REPLACE" in str(stmt):
            sess.functions.update(BOTH)
        return await original(stmt, params)

    sess.execute = execute  # type: ignore[method-assign]
    await _access(dbs).ensure_gap_functions("p")


async def test_bootstrap_failure_without_functions_raises() -> None:
    err = ProgrammingError("CREATE", {}, Exception("permission denied"))
    dbs = FakePlantDatabases(FakePlantSession(create_error=err))
    with pytest.raises(PlantFunctionsUnavailable) as exc:
        await _access(dbs).ensure_gap_functions("p")
    assert exc.value.code == "PLANT_FUNCTIONS_UNAVAILABLE"
