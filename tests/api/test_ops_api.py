"""Readiness, access log and cached scale reads over HTTP (agent.md §7.7, §7.9)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps
from app.core.cache import TTLCache
from app.core.config import Settings
from app.domain.static_config import parse_static_config
from app.main import create_app
from app.models import Plant
from tests.repositories.conftest import make_engine
from tests.unit.fakes import FakePlantDatabases, FakeResult

STATIC = parse_static_config(
    "plants: {kochi: Kochi}\nplant_name: X\ncarousal_speed: {1: 50, 2: 48}\n"
    "interruption_to_exclude_minutes: 5\nplants_with_device_id: []\n"
)
SHIPPED = Path(__file__).resolve().parents[2] / "config" / "static_plant_config.yaml"
T0, T1 = datetime(2026, 1, 5, 14, 0, 0), datetime(2026, 1, 5, 15, 0, 0)


def _settings(**kw: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        central_database_url="mysql+aiomysql://u:p@h/d",
        jwt_secret_key="k" * 40,
        auth_enabled=False,
        **kw,
    )


# ---- readiness --------------------------------------------------------------------------------


async def _ready(static_path: str, healthy_db: bool) -> httpx.Response:
    settings = _settings(static_plant_config_path=static_path)
    app = create_app(settings)
    engine = (
        create_async_engine("sqlite+aiosqlite://")
        if healthy_db
        else create_async_engine("postgresql+psycopg://u:p@127.0.0.1:1/x")
    )
    app.state.settings = settings
    app.state.central_engine = engine
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.get("/api/v1/health/ready")
    finally:
        await engine.dispose()


async def test_ready_ok_when_db_and_static_config_are_fine() -> None:
    response = await _ready(str(SHIPPED), healthy_db=True)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"central_db": True, "static_config": True}}


async def test_ready_503_when_static_config_is_missing() -> None:
    response = await _ready("does/not/exist.yaml", healthy_db=True)
    assert response.status_code == 503
    assert response.json()["checks"] == {"central_db": True, "static_config": False}


async def test_ready_503_when_central_db_is_down() -> None:
    response = await _ready(str(SHIPPED), healthy_db=False)
    assert response.status_code == 503
    assert response.json()["checks"]["central_db"] is False


# ---- access log ----------------------------------------------------------------------------------


async def test_access_log_has_method_path_status_duration_and_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(_settings())
    with caplog.at_level(logging.INFO, logger="prodash.access"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.get("/api/v1/health/live?token=SHOULD-NOT-BE-LOGGED", headers={"X-Request-ID": "rid-42"})
    line = next(r.getMessage() for r in caplog.records if r.name == "prodash.access")
    assert "method=GET" in line and "path=/api/v1/health/live" in line and "status=200" in line
    assert "duration_ms=" in line and "request_id=rid-42" in line
    assert "SHOULD-NOT-BE-LOGGED" not in line  # the query string is not logged


# ---- cached scale reads ------------------------------------------------------------------------------


class Scripted:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        text = str(stmt)
        if "first_cyl_time" in text:
            return FakeResult([SimpleNamespace(system_id=1, first_cyl_time=T0, last_cyl_time=T1)])  # type: ignore[list-item]
        self.calls += 1
        return FakeResult([SimpleNamespace(machine_id=1, system_id=1, device_id=1, cnt=30)])  # type: ignore[list-item]


@pytest.fixture
async def cached_client() -> AsyncIterator[tuple[httpx.AsyncClient, Scripted, Any]]:
    engine = await make_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as seed:
        seed.add(Plant(id=1, short_name="kochi", plant_name="Kochi", zone="S", host_ip="10.0.0.1",
                       db_user="ro", db_password="pw", db_database="ccc"))  # fmt: skip
        await seed.commit()
    settings = _settings()
    app = create_app(settings)

    async def central() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    scripted = Scripted()
    dbs = FakePlantDatabases(scripted)  # type: ignore[arg-type]
    app.dependency_overrides[deps.get_app_settings] = lambda: settings
    app.dependency_overrides[deps.get_central_session] = central
    app.dependency_overrides[deps.get_static_config] = lambda: STATIC
    app.dependency_overrides[deps.get_plant_databases] = lambda: dbs
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, scripted, app
    await engine.dispose()


PATH = "/prodash/getUnderPerformingScales/kochi/10/0/1767599400/1767603000"


async def test_scale_reads_are_not_cached_by_default(cached_client: Any) -> None:
    client, scripted, _ = cached_client
    for _ in range(3):
        assert (await client.get(PATH)).status_code == 200
    assert scripted.calls == 3


async def test_scale_reads_are_served_from_cache_when_enabled(cached_client: Any) -> None:
    client, scripted, app = cached_client
    app.state.response_cache = TTLCache(30)
    bodies = [(await client.get(PATH)).json() for _ in range(3)]
    assert bodies[0] == bodies[1] == bodies[2] and bodies[0]["rows"]
    assert scripted.calls == 1  # one plant query for three requests

    other = "/prodash/getUnderPerformingScales/kochi/5/0/1767599400/1767603000"
    await client.get(other)  # different row_count -> different key
    assert scripted.calls == 2
