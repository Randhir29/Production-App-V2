"""Golden-master parity for the central-DB-only plant endpoints (agent.md §9.3, E02/E03/E04).

Each ``golden/plants/<case>.json`` is captured from the running PHP app with the central rows
that produced it::

    {"path": "/getPlants2",
     "plants":    [{"id": 1, "short_name": "anantapur", "plant_name": "...", "zone": "South"}],
     "carousals": [{"id": 1, "plant_id": 1, "carousal_id": 1, "heads": 72, "rated_productivity": 3000}],
     "expected":  {"plants": [{"value": "anantapur", "text": "...", "zone": "South", "carousals_count": 1}]}}

Numeric strings in ``expected`` are normalised (the PHP API returned DB numbers as strings).
Skipped until golden files are captured. Connectivity latency is non-deterministic and is
covered by tests/unit/test_connectivity.py instead.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import deps
from app.core.config import Settings
from app.domain.static_config import parse_static_config
from app.main import create_app
from app.models import Carousel, Plant
from tests.repositories.conftest import make_engine

pytestmark = pytest.mark.parity

CASES = sorted((Path(__file__).parent / "golden" / "plants").glob("*.json"))
STATIC = parse_static_config(
    "plants: {}\nplant_name: Mysore LPG Plant\ncarousal_speed: {1: 50}\n"
    "interruption_to_exclude_minutes: 5\nplants_with_device_id: []\n"
)


def _normalise(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
    return value


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = await make_engine()
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.skipif(not CASES, reason="no PHP golden files captured yet (tests/parity/golden)")
@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
async def test_matches_php(case: Path, sessions: async_sessionmaker[AsyncSession]) -> None:
    golden = json.loads(case.read_text(encoding="utf-8"))
    async with sessions() as seed:
        for model, key in ((Plant, "plants"), (Carousel, "carousals")):
            if golden.get(key):
                await seed.execute(insert(model), golden[key])
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

    app.dependency_overrides[deps.get_app_settings] = lambda: settings
    app.dependency_overrides[deps.get_central_session] = central
    app.dependency_overrides[deps.get_static_config] = lambda: STATIC

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        response = await c.get(f"/prodash{golden['path']}")
    assert response.status_code == 200
    assert _normalise(response.json()) == _normalise(golden["expected"])
