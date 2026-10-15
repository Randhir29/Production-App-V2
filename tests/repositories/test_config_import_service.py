"""ConfigImportService against an in-memory central DB (agent.md §4.1, D-11, D-23)."""

from __future__ import annotations

import csv
import io
import json

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli.config_commands import format_import_report
from app.domain.carousel_csv import parse_carousel_csv
from app.domain.php_connections import PhpConnection
from app.models import Carousel, CarouselBreak, Plant
from app.repositories.central.carousel_repo import CarouselRepository
from app.repositories.central.config_import_repo import ConfigImportRepository
from app.repositories.central.plant_repo import PlantRepository
from app.services.carousel_config import CarouselConfigService
from app.services.config_import import ConfigImportService

HEADER = ["sap_id", "carousal_id", "heads", "rated_productivity", "production_hrs", "breaks"]


def csv_text(*rows: tuple[str, int, int, int, tuple[str, str], list[tuple[str, str]]]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEADER)
    for sap, car, heads, rated, (start, stop), breaks in rows:
        w.writerow(
            [
                sap, car, heads, rated,
                json.dumps([{"start_time": start, "stop_time": stop, "shift_name": "Shift 1"}]),
                json.dumps([{"start_time": a, "stop_time": b} for a, b in breaks]),
            ]
        )  # fmt: skip
    return out.getvalue()


@pytest.fixture
async def session_with_plants(central_session: AsyncSession) -> AsyncSession:
    await central_session.execute(
        insert(Plant),
        [
            {"id": 1, "short_name": "anantapur", "erp_id": "2935", "host_ip": "old-host",
             "db_user": "old", "db_password": "old-pw", "db_database": "ccc", "use_device_id": False},
            {"id": 2, "short_name": "kochi", "erp_id": "2401", "use_device_id": False},
        ],
    )  # fmt: skip
    await central_session.commit()
    return central_session


def svc(session: AsyncSession) -> ConfigImportService:
    return ConfigImportService(ConfigImportRepository(session))


def conn(short: str, host: str = "10.0.0.1", pw: str = "s3cret", port: int | None = 5432) -> PhpConnection:
    return PhpConnection(short, host, "readonly", pw, "ccc", port)


# ---- plant connections -------------------------------------------------------------------------


async def test_connections_dry_run_then_apply_then_idempotent(session_with_plants: AsyncSession) -> None:
    s = session_with_plants
    conns = [conn("anantapur", host="10.4.1.2"), conn("kochi", host="10.4.1.3"), conn("ghost")]

    dry = await svc(s).import_plant_connections(conns, dry_run=True)
    assert dry.updated == ["anantapur", "kochi"] and dry.unmatched == ["ghost"]
    s.expire_all()
    assert (await s.execute(select(Plant.host_ip).where(Plant.id == 1))).scalar_one() == "old-host"

    applied = await svc(s).import_plant_connections(conns, dry_run=False)
    assert applied.updated == ["anantapur", "kochi"]
    s.expire_all()
    row = (await s.execute(select(Plant).where(Plant.id == 2))).scalar_one()
    assert (row.host_ip, row.db_user, row.db_password, row.db_database) == (
        "10.4.1.3", "readonly", "s3cret", "ccc",
    )  # fmt: skip

    again = await svc(s).import_plant_connections(conns, dry_run=False)
    assert again.updated == [] and again.unchanged == ["anantapur", "kochi"]


async def test_create_missing_inserts_with_empty_name_and_zone(session_with_plants: AsyncSession) -> None:
    s = session_with_plants
    report = await svc(s).import_plant_connections([conn("newplant")], dry_run=False, create_missing=True)
    assert report.inserted == ["newplant"]
    row = (await s.execute(select(Plant).where(Plant.short_name == "newplant"))).scalar_one()
    assert row.plant_name is None and row.zone is None and row.host_ip == "10.0.0.1"


async def test_port_mismatch_is_reported_not_stored(session_with_plants: AsyncSession) -> None:
    report = await svc(session_with_plants).import_plant_connections(
        [conn("anantapur", port=5433)], dry_run=True, expected_port=5432
    )
    assert any("5433" in w and "PLANT_DB_PORT" in w for w in report.warnings)


async def test_report_text_never_contains_credentials(session_with_plants: AsyncSession) -> None:
    report = await svc(session_with_plants).import_plant_connections(
        [conn("anantapur", host="10.4.1.2", pw="TOP-SECRET")], dry_run=True
    )
    text = format_import_report("import-plant-connections", report)
    assert "TOP-SECRET" not in text and "10.4.1.2" not in text and "readonly" not in text


# ---- carousel config ---------------------------------------------------------------------------


async def test_carousel_import_end_to_end_matches_section_4_1(session_with_plants: AsyncSession) -> None:
    s = session_with_plants
    parsed = parse_carousel_csv(
        csv_text(
            ("2935", 1, 24, 1660, ("14:00:00", "22:30:00"), [("19:00:00", "19:30:00")]),
            (
                "2935",
                2,
                24,
                1820,
                ("06:00:00", "22:30:00"),
                [("09:30:00", "10:00:00"), ("19:00:00", "19:30:00")],
            ),
            ("2401", 1, 48, 3200, ("06:40:00", "23:10:00"), [("11:30:00", "12:00:00")]),
            ("9999", 1, 24, 1600, ("06:00:00", "14:00:00"), []),
        )
    )
    report = await svc(s).import_carousels(parsed.carousels, dry_run=False)
    assert report.inserted == ["anantapur/1", "anantapur/2", "kochi/1"]
    assert report.unmatched == ["9999/1"]

    config = await CarouselConfigService(PlantRepository(s), CarouselRepository(s)).get_config("anantapur")
    assert config == {
        1: {
            "heads": 24,
            "stdOutput": 1660,
            "times": {"start": "14:00:00", "end": "22:30:00",
                      "breaks": [{"from": "19:00:00", "to": "19:30:00"}]},
        },
        2: {
            "heads": 24,
            "stdOutput": 1820,
            "times": {"start": "06:00:00", "end": "22:30:00",
                      "breaks": [{"from": "09:30:00", "to": "10:00:00"},
                                 {"from": "19:00:00", "to": "19:30:00"}]},
        },
    }  # fmt: skip


async def test_carousel_import_is_idempotent_and_applies_changes(session_with_plants: AsyncSession) -> None:
    s = session_with_plants
    first = parse_carousel_csv(
        csv_text(("2935", 1, 24, 1660, ("14:00:00", "22:30:00"), [("19:00:00", "19:30:00")]))
    )
    await svc(s).import_carousels(first.carousels, dry_run=False)
    same = await svc(s).import_carousels(first.carousels, dry_run=False)
    assert same.unchanged == ["anantapur/1"] and not same.updated and not same.inserted

    changed = parse_carousel_csv(
        csv_text(("2935", 1, 24, 1700, ("14:00:00", "22:30:00"),
                  [("19:00:00", "19:30:00"), ("21:00:00", "21:15:00")]))
    )  # fmt: skip
    report = await svc(s).import_carousels(changed.carousels, dry_run=False)
    assert report.updated == ["anantapur/1"]
    s.expire_all()
    car = (await s.execute(select(Carousel))).scalars().all()
    assert len(car) == 1 and car[0].rated_productivity == 1700
    assert len((await s.execute(select(CarouselBreak))).scalars().all()) == 2  # replaced, not appended


async def test_carousel_dry_run_writes_nothing(session_with_plants: AsyncSession) -> None:
    s = session_with_plants
    parsed = parse_carousel_csv(
        csv_text(("2935", 1, 24, 1660, ("14:00:00", "22:30:00"), [("19:00:00", "19:30:00")]))
    )
    report = await svc(s).import_carousels(parsed.carousels, dry_run=True)
    assert report.dry_run and report.inserted == ["anantapur/1"]
    assert (await s.execute(select(Carousel))).first() is None
    assert (await s.execute(select(CarouselBreak))).first() is None


async def test_sap_to_short_name_map(session_with_plants: AsyncSession) -> None:
    assert await svc(session_with_plants).sap_to_short_name() == {"2935": "anantapur", "2401": "kochi"}
