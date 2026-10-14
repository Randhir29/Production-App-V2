"""Golden-master parity for ``getCarousalConfig`` (agent.md §9.3, release gate P1).

Each ``golden/carousel_config/<case>.json`` file is captured from the running PHP app together
with the central-DB rows it was produced from::

    {"plant": "anantapur",
     "plants":    [{"id": 1, "short_name": "anantapur"}],
     "carousals": [{"id": 1, "plant_id": 1, "carousal_id": 1, "heads": 72,
                    "rated_productivity": 3000, "start_time": "14:00:00", "stop_time": "22:30:00"}],
     "breaks":    [{"id": 1, "plant_id": 1, "carousal_id": 1,
                    "start_time": "19:00:00", "stop_time": "19:30:00"}],
     "expected":  {"1": {"heads": 72, "stdOutput": 3000, "times": {...}}}}

The suite skips until golden files have been captured from PHP.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import time
from pathlib import Path

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.carousel_config import parse_time
from app.models import Carousel, CarouselBreak, Plant
from app.repositories.central.carousel_repo import CarouselRepository
from app.repositories.central.plant_repo import PlantRepository
from app.services.carousel_config import CarouselConfigService
from tests.repositories.conftest import make_engine

pytestmark = pytest.mark.parity

GOLDEN_DIR = Path(__file__).parent / "golden" / "carousel_config"
CASES = sorted(GOLDEN_DIR.glob("*.json"))


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = await make_engine()
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _times(row: dict[str, object]) -> dict[str, object]:
    out = dict(row)
    for key in ("start_time", "stop_time"):
        if key in out:
            value: time | None = parse_time(out[key])  # type: ignore[arg-type]
            out[key] = value
    return out


@pytest.mark.skipif(not CASES, reason="no PHP golden files captured yet (tests/parity/golden)")
@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
async def test_matches_php_get_carousal_config(case: Path, session: AsyncSession) -> None:
    golden = json.loads(case.read_text(encoding="utf-8"))
    for model, key in ((Plant, "plants"), (Carousel, "carousals"), (CarouselBreak, "breaks")):
        rows = [_times(r) for r in golden.get(key, [])]
        if rows:
            await session.execute(insert(model), rows)
    await session.commit()

    service = CarouselConfigService(PlantRepository(session), CarouselRepository(session))
    actual = await service.get_config(golden["plant"])

    # JSON object keys are strings; PHP key order is table order, which dicts preserve.
    assert json.loads(json.dumps(actual)) == golden["expected"]
    assert list(json.loads(json.dumps(actual))) == list(golden["expected"])
