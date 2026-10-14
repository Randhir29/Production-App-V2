"""Unit tests for the §4.1 carousel configuration shape and CarouselConfigService (no I/O)."""

from __future__ import annotations

from datetime import time, timedelta

import pytest

from app.core.errors import PlantConfigMissing, PlantNotFound
from app.domain.carousel_config import (
    BreakRow,
    CarouselRow,
    build_carousel_config,
    carousel_ids,
    format_time,
    parse_time,
)
from app.domain.legacy_mapping import role_for_group
from app.repositories.central.plant_repo import PlantRecord
from app.services.carousel_config import CarouselConfigService

# ---- format_time / parse_time --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (time(14, 0, 0), "14:00:00"),
        (time(9, 5, 7, 123456), "09:05:07"),
        (timedelta(hours=19, minutes=30), "19:30:00"),  # aiomysql returns TIME as timedelta
        (timedelta(seconds=0), "00:00:00"),
        (timedelta(hours=22, minutes=30, seconds=5, microseconds=900), "22:30:05"),
        ("7:05:09", "07:05:09"),
        ("22:30:00.250", "22:30:00"),
    ],
)
def test_format_time(value: object, expected: str | None) -> None:
    assert format_time(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["25:61:00", "noon", "", timedelta(seconds=-1)])
def test_format_time_rejects_garbage(bad: object) -> None:
    with pytest.raises(ValueError):
        format_time(bad)  # type: ignore[arg-type]


def test_parse_time_round_trip_and_range() -> None:
    assert parse_time(timedelta(hours=6, minutes=15)) == time(6, 15)
    assert parse_time(None) is None
    with pytest.raises(ValueError):
        parse_time(timedelta(hours=24))


# ---- build_carousel_config -----------------------------------------------------------


def test_config_matches_agent_md_shape_for_anantapur_like_plant() -> None:
    """Start 14:00, break 19:00-19:30, end 22:30 (the §9.1 anantapur example)."""
    config = build_carousel_config(
        [
            CarouselRow(1, 72, 3000, "14:00:00", "22:30:00"),
            CarouselRow(2, 48, 2000, None, None),
        ],
        [BreakRow(1, "19:00:00", "19:30:00")],
    )
    assert config == {
        1: {
            "heads": 72,
            "stdOutput": 3000,
            "times": {
                "start": "14:00:00",
                "end": "22:30:00",
                "breaks": [{"from": "19:00:00", "to": "19:30:00"}],
            },
        },
        2: {"heads": 48, "stdOutput": 2000, "times": {"start": None, "end": None, "breaks": None}},
    }


def test_breaks_null_when_carousel_has_none() -> None:
    config = build_carousel_config([CarouselRow(1, 24, 1800, "06:00:00", "14:00:00")], [])
    assert config[1]["times"]["breaks"] is None


def test_breaks_keep_input_order_and_belong_to_their_carousel() -> None:
    config = build_carousel_config(
        [CarouselRow(1, 24, 1800, "06:00:00", "14:00:00"), CarouselRow(2, 24, 1800, None, None)],
        [
            BreakRow(2, "10:00:00", "10:10:00"),
            BreakRow(1, "12:00:00", "12:30:00"),
            BreakRow(1, "08:00:00", "08:15:00"),
            BreakRow(9, "01:00:00", "01:10:00"),  # no such carousel: ignored
        ],
    )
    assert config[1]["times"]["breaks"] == [
        {"from": "12:00:00", "to": "12:30:00"},
        {"from": "08:00:00", "to": "08:15:00"},
    ]
    assert config[2]["times"]["breaks"] == [{"from": "10:00:00", "to": "10:10:00"}]
    assert 9 not in config


def test_carousels_keyed_in_table_order() -> None:
    config = build_carousel_config([CarouselRow(2, 24, 1, None, None), CarouselRow(1, 24, 1, None, None)], [])
    assert carousel_ids(config) == [2, 1]


def test_repeated_carousel_id_last_row_wins_first_position_kept() -> None:
    """PHP array assignment semantics for a duplicated key."""
    config = build_carousel_config(
        [
            CarouselRow(1, 12, 100, None, None),
            CarouselRow(2, 24, 200, None, None),
            CarouselRow(1, 48, 300, None, None),
        ],
        [],
    )
    assert list(config) == [1, 2]
    assert config[1]["heads"] == 48


def test_empty_inputs() -> None:
    assert build_carousel_config([], []) == {}


# ---- role mapping --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("group", "role"),
    [
        ("admin", "admin"),
        ("officers", "officer"),
        ("security", "viewer"),
        ("dataentry", "viewer"),
        (" Admin ", "admin"),
        ("unknown", None),
    ],
)
def test_role_for_group(group: str, role: str | None) -> None:
    assert role_for_group(group) == role


# ---- CarouselConfigService with fakes ------------------------------------------------


class FakePlants:
    def __init__(self, known: dict[str, int]) -> None:
        self._known = known

    async def get_by_short_name(self, short_name: str) -> PlantRecord | None:
        if short_name not in self._known:
            return None
        return PlantRecord(self._known[short_name], short_name, None, None, None, None, None, None)


class FakeCarousels:
    def __init__(self, carousels: dict[int, list[CarouselRow]], breaks: dict[int, list[BreakRow]]):
        self._c, self._b = carousels, breaks

    async def list_carousels(self, plant_id: int) -> list[CarouselRow]:
        return self._c.get(plant_id, [])

    async def list_breaks(self, plant_id: int) -> list[BreakRow]:
        return self._b.get(plant_id, [])


def _service() -> CarouselConfigService:
    return CarouselConfigService(
        FakePlants({"anantapur": 1, "empty": 2}),  # type: ignore[arg-type]
        FakeCarousels(  # type: ignore[arg-type]
            {1: [CarouselRow(1, 72, 3000, "14:00:00", "22:30:00"), CarouselRow(2, 24, 1500, None, None)]},
            {1: [BreakRow(1, "19:00:00", "19:30:00")]},
        ),
    )


async def test_service_get_config() -> None:
    config = await _service().get_config("anantapur")
    assert set(config) == {1, 2}
    assert config[1]["times"]["breaks"] == [{"from": "19:00:00", "to": "19:30:00"}]


async def test_service_unknown_plant_raises_not_found() -> None:
    with pytest.raises(PlantNotFound):
        await _service().get_config("nowhere")


async def test_service_plant_without_carousels_gives_empty_config() -> None:
    assert await _service().get_config("empty") == {}


async def test_service_get_carousels_ids_and_missing_config() -> None:
    assert await _service().get_carousels("anantapur") == [1, 2]
    with pytest.raises(PlantConfigMissing) as excinfo:
        await _service().get_carousels("empty")
    assert excinfo.value.http_status == 409
    assert excinfo.value.code == "PLANT_CONFIG_INCOMPLETE"
