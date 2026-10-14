"""ConnectivityService (agent.md §5.8, D-13): payloads, ordering of checks, latency rounding."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.domain.static_config import parse_static_config
from app.repositories.central.plant_repo import PlantRecord
from app.services import connectivity as connectivity_module
from app.services.connectivity import ConnectivityService
from app.services.plant_access import PlantAccess
from tests.unit.fakes import FakePlantDatabases, FakePlantSession

STATIC = parse_static_config(
    """
plants: {anantapur: Anantapur, kochi: Kochi, nocreds: NoCreds}
plant_name: X
carousal_speed: {1: 50}
interruption_to_exclude_minutes: 5
plants_with_device_id: []
"""
)

RECORDS = {
    "anantapur": PlantRecord(1, "anantapur", "A", "South", "10.0.0.1", "ro", "pw", "ccc"),
    "kochi": PlantRecord(2, "kochi", "K", "South", "10.0.0.2", "ro", "pw", "ccc"),
    "nocreds": PlantRecord(3, "nocreds", "N", "South", None, None, None, None),
    "dbonly": PlantRecord(4, "dbonly", "D", "South", "10.0.0.4", "ro", "pw", "ccc"),  # not static
}


class FakePlants:
    async def get_by_short_name(self, name: str) -> PlantRecord | None:
        return RECORDS.get(name)


def _service(databases: FakePlantDatabases) -> ConnectivityService:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="k" * 40,
    )
    access = PlantAccess(FakePlants(), databases, settings)  # type: ignore[arg-type]
    return ConnectivityService(access, STATIC)


def _oe() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("could not connect to 10.0.0.1 pw"))


async def test_success_payload_has_latency() -> None:
    dbs = FakePlantDatabases(FakePlantSession())
    result = await _service(dbs).check("anantapur")
    assert result["status"] == "success"
    assert result["message"] == "Connection Successful!"
    assert isinstance(result["latency"], float) and result["latency"] >= 0
    assert dbs.ro_targets[0]["host"] == "10.0.0.1" and dbs.ro_targets[0]["port"] == 5432
    assert dbs.ro_targets[0]["dbname"] == "ccc"


async def test_latency_is_rounded_half_away_from_zero_in_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = itertools.chain([100.0, 100.0125], itertools.repeat(100.0125))  # 12.5 ms
    monkeypatch.setattr(connectivity_module.time, "perf_counter", lambda: next(ticks))
    result = await _service(FakePlantDatabases(FakePlantSession())).check("anantapur")
    assert result["latency"] == 13.0  # built-in round(12.5) would give 12


@pytest.mark.parametrize("plant", [None, "", "nowhere", "dbonly", "nocreds"])
async def test_unknown_plant_variants(plant: str | None) -> None:
    dbs = FakePlantDatabases(FakePlantSession())
    assert await _service(dbs).check(plant) == {"status": "failure", "message": "Unknown Plant"}
    assert dbs.ro_targets == []  # never connects for an unknown plant


async def test_no_row_is_connection_error() -> None:
    dbs = FakePlantDatabases(FakePlantSession(probe_rows=[]))
    assert await _service(dbs).check("kochi") == {"status": "failure", "message": "Connection Error"}


@pytest.mark.parametrize("where", ["connect", "query"])
async def test_exceptions_give_generic_failure_without_internals(where: str) -> None:
    dbs = (
        FakePlantDatabases(FakePlantSession(), connect_error=_oe())
        if where == "connect"
        else FakePlantDatabases(FakePlantSession(raise_on_execute=_oe()))
    )
    result: dict[str, Any] = await _service(dbs).check("kochi")
    assert result == {"status": "failure", "message": "Connection Error"}
    assert "10.0.0.1" not in str(result) and "pw" not in str(result)
