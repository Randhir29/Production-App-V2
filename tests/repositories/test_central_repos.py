"""Repository + service tests against an in-memory central DB (agent.md §9.2, §4.1, §5.8)."""

from __future__ import annotations

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Carousel, CarouselBreak, Plant
from app.repositories.central.carousel_repo import CarouselRepository
from app.repositories.central.plant_repo import PlantRepository
from app.services.carousel_config import CarouselConfigService


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            Plant(id=1, short_name="anantapur", plant_name="Anantapur", zone="South",
                  host_ip="10.0.0.1", db_user="ro", db_password="secret", db_database="ccc"),
            Plant(id=2, short_name="kochi", plant_name="Kochi", zone="South"),
            Plant(id=3, short_name="bare", plant_name="Bare", zone="West"),
            Carousel(id=10, plant_id=1, carousal_id=1, heads=72, rated_productivity=3000,
                     start_time=time(14, 0), stop_time=time(22, 30)),
            Carousel(id=11, plant_id=1, carousal_id=2, heads=24, rated_productivity=1500),
            Carousel(id=12, plant_id=2, carousal_id=1, heads=24, rated_productivity=1800,
                     start_time=time(6, 0), stop_time=time(14, 0)),
            CarouselBreak(id=1, plant_id=1, carousal_id=1, start_time=time(19, 0),
                          stop_time=time(19, 30)),
            CarouselBreak(id=2, plant_id=2, carousal_id=1, start_time=time(10, 0),
                          stop_time=time(10, 15)),
        ]
    )  # fmt: skip
    await session.commit()


async def test_plant_by_short_name_hides_password_in_repr(central_session: AsyncSession) -> None:
    await _seed(central_session)
    plant = await PlantRepository(central_session).get_by_short_name("anantapur")
    assert plant is not None
    assert plant.id == 1 and plant.db_password == "secret"
    assert "secret" not in repr(plant)
    assert await PlantRepository(central_session).get_by_short_name("nope") is None


async def test_registry_counts_carousels_and_is_ordered(central_session: AsyncSession) -> None:
    await _seed(central_session)
    rows = await PlantRepository(central_session).list_registry()
    assert [(r.value, r.carousals_count) for r in rows] == [
        ("anantapur", 2),
        ("bare", 0),
        ("kochi", 1),
    ]
    assert rows[0].text == "Anantapur" and rows[0].zone == "South"


async def test_config_by_short_name_requires_exactly_one_row(central_session: AsyncSession) -> None:
    await _seed(central_session)
    repo = PlantRepository(central_session)
    row = await repo.get_config_by_short_name("kochi")
    assert row is not None and row.carousals_count == 1
    assert await repo.get_config_by_short_name("missing") is None

    central_session.add(Plant(id=4, short_name="kochi", plant_name="Dup", zone="X"))
    await central_session.commit()
    assert await repo.get_config_by_short_name("kochi") is None


async def test_carousel_repo_orders_by_id_and_formats_times(central_session: AsyncSession) -> None:
    await _seed(central_session)
    repo = CarouselRepository(central_session)
    carousels = await repo.list_carousels(1)
    assert [c.carousal_id for c in carousels] == [1, 2]
    assert (carousels[0].start_time, carousels[0].stop_time) == ("14:00:00", "22:30:00")
    assert carousels[1].start_time is None
    breaks = await repo.list_breaks(1)
    assert [(b.carousal_id, b.start_time, b.stop_time) for b in breaks] == [(1, "19:00:00", "19:30:00")]
    assert await repo.list_carousels(999) == []


async def test_service_end_to_end_matches_section_4_1_shape(central_session: AsyncSession) -> None:
    await _seed(central_session)
    service = CarouselConfigService(PlantRepository(central_session), CarouselRepository(central_session))
    assert await service.get_config("anantapur") == {
        1: {
            "heads": 72,
            "stdOutput": 3000,
            "times": {
                "start": "14:00:00",
                "end": "22:30:00",
                "breaks": [{"from": "19:00:00", "to": "19:30:00"}],
            },
        },
        2: {"heads": 24, "stdOutput": 1500, "times": {"start": None, "end": None, "breaks": None}},
    }
    assert await service.get_config("bare") == {}
