"""E01-E06 over HTTP: v1 and legacy routers share services (agent.md §6, §9.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.config import Settings
from app.domain.static_config import parse_static_config
from app.main import create_app
from app.models import Carousel, Plant
from tests.repositories.conftest import make_engine
from tests.unit.fakes import FakePlantDatabases, FakePlantSession

STATIC = parse_static_config(
    """
plants: {kochi: Kochi LPG, anantapur: Anantapur LPG}
plant_name: Mysore LPG Plant
carousal_speed: {1: 50, 2: 48}
interruption_to_exclude_minutes: 5
plants_with_device_id: [anantapur]
"""
)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    engine = await make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as seed:
        seed.add_all(
            [
                Plant(id=1, short_name="anantapur", plant_name="Anantapur", zone="South",
                      host_ip="10.0.0.1", db_user="ro", db_password="pw", db_database="ccc"),
                Plant(id=2, short_name="kochi", plant_name="Kochi", zone="South",
                      host_ip="10.0.0.2", db_user="ro", db_password="pw", db_database="ccc"),
                Plant(id=3, short_name="delhi", plant_name="Delhi", zone="North"),
                Carousel(id=1, plant_id=1, carousal_id=1, heads=72, rated_productivity=3000),
                Carousel(id=2, plant_id=1, carousal_id=2, heads=24, rated_productivity=1500),
                Carousel(id=3, plant_id=2, carousal_id=1, heads=24, rated_productivity=1800),
            ]
        )  # fmt: skip
        await seed.commit()

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="k" * 40,
        auth_enabled=False,
    )
    app = create_app(settings)

    async def central() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    plant_dbs = FakePlantDatabases(FakePlantSession())
    app.dependency_overrides[deps.get_app_settings] = lambda: settings
    app.dependency_overrides[deps.get_central_session] = central
    app.dependency_overrides[deps.get_static_config] = lambda: STATIC
    app.dependency_overrides[deps.get_plant_databases] = lambda: plant_dbs
    app.state.fake_plant_dbs = plant_dbs

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await engine.dispose()


async def _both(client: httpx.AsyncClient, v1: str, legacy: str) -> tuple[Any, Any]:
    a = await client.get(f"/api/v1{v1}")
    b = await client.get(f"/prodash{legacy}")
    assert a.status_code == 200 and b.status_code == 200
    return a.json(), b.json()


async def test_static_plants_sorted_by_key(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/plants", "/getPlants")
    expected = {
        "plants": [
            {"value": "anantapur", "text": "Anantapur LPG"},
            {"value": "kochi", "text": "Kochi LPG"},
        ]
    }
    assert v1 == legacy == expected


async def test_registry_with_carousel_counts(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/plants/registry", "/getPlants2")
    assert v1 == legacy
    assert v1["plants"] == [
        {"value": "anantapur", "text": "Anantapur", "zone": "South", "carousals_count": 2},
        {"value": "delhi", "text": "Delhi", "zone": "North", "carousals_count": 0},
        {"value": "kochi", "text": "Kochi", "zone": "South", "carousals_count": 1},
    ]


async def test_zone_map(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/plants/zone-map", "/zone-maps")
    assert (
        v1
        == legacy
        == [
            {"zone": "South", "plant": "anantapur"},
            {"zone": "North", "plant": "delhi"},
            {"zone": "South", "plant": "kochi"},
        ]
    )


async def test_plant_name(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/meta/plant-name", "/getPlantName")
    assert v1 == legacy == {"name": "Mysore LPG Plant"}


async def test_client_device_has_client_key(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/meta/client-device", "/getDeviceName")
    assert set(v1) == set(legacy) == {"client"}


async def test_connectivity_success_shape(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/plants/kochi/connectivity", "/getConnectivityStatus/kochi")
    for body in (v1, legacy):
        assert body["status"] == "success"
        assert body["message"] == "Connection Successful!"
        assert isinstance(body["latency"], int | float)


async def test_connectivity_unknown_plant_is_200_failure(client: httpx.AsyncClient) -> None:
    v1, legacy = await _both(client, "/plants/nowhere/connectivity", "/getConnectivityStatus/nowhere")
    assert v1 == legacy == {"status": "failure", "message": "Unknown Plant"}


async def test_legacy_connectivity_without_plant(client: httpx.AsyncClient) -> None:
    r = await client.get("/prodash/getConnectivityStatus")
    assert r.json() == {"status": "failure", "message": "Unknown Plant"}


async def test_connectivity_no_row_and_no_latency_key(client: httpx.AsyncClient) -> None:
    client._transport.app.state.fake_plant_dbs.shared.probe_rows = []  # type: ignore[attr-defined]
    r = await client.get("/api/v1/plants/kochi/connectivity")
    assert r.json() == {"status": "failure", "message": "Connection Error"}


async def test_invalid_plant_segment_rejected_with_envelope(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/plants/Bad_Name/connectivity")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
