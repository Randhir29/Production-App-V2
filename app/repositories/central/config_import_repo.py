"""Central-DB writes used by the configuration import CLIs (agent.md §4.1, §5.9). SQL only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Carousel, CarouselBreak, Plant


@dataclass(frozen=True, slots=True)
class PlantConnectionRow:
    """A plant with its connection fields; the password is excluded from ``repr``."""

    id: int
    short_name: str
    erp_id: str | None
    host_ip: str | None
    db_user: str | None
    db_password: str | None = field(repr=False)
    db_database: str | None


class ConfigImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    # ---- plants ---------------------------------------------------------------------------

    async def list_plants(self) -> list[PlantConnectionRow]:
        rows = (await self._session.execute(select(Plant).order_by(Plant.id))).scalars().all()
        return [
            PlantConnectionRow(
                p.id, p.short_name, p.erp_id, p.host_ip, p.db_user, p.db_password, p.db_database
            )
            for p in rows
        ]

    async def set_connection(
        self, plant_id: int, host_ip: str, db_user: str, db_password: str, db_database: str
    ) -> None:
        plant = await self._session.get(Plant, plant_id)
        if plant is None:
            raise LookupError(f"plant {plant_id} not found")
        plant.host_ip = host_ip
        plant.db_user = db_user
        plant.db_password = db_password
        plant.db_database = db_database

    async def create_plant(
        self, short_name: str, host_ip: str, db_user: str, db_password: str, db_database: str
    ) -> int:
        plant = Plant(
            short_name=short_name,
            host_ip=host_ip,
            db_user=db_user,
            db_password=db_password,
            db_database=db_database,
            use_device_id=False,
        )
        self._session.add(plant)
        await self._session.flush()
        return plant.id

    # ---- carousals / breaks -----------------------------------------------------------------

    async def upsert_carousel(
        self,
        plant_id: int,
        carousal_id: int,
        heads: int,
        rated_productivity: int,
        start: time,
        stop: time,
    ) -> str:
        """Insert or update the carousel row; returns ``inserted``, ``updated`` or ``unchanged``."""
        rows = (
            (
                await self._session.execute(
                    select(Carousel)
                    .where(Carousel.plant_id == plant_id, Carousel.carousal_id == carousal_id)
                    .order_by(Carousel.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            self._session.add(
                Carousel(
                    plant_id=plant_id,
                    carousal_id=carousal_id,
                    heads=heads,
                    rated_productivity=rated_productivity,
                    start_time=start,
                    stop_time=stop,
                )
            )
            await self._session.flush()
            return "inserted"
        current = rows[0]
        same = (
            current.heads == heads
            and current.rated_productivity == rated_productivity
            and current.start_time == start
            and current.stop_time == stop
        )
        if same:
            return "unchanged"
        current.heads = heads
        current.rated_productivity = rated_productivity
        current.start_time = start
        current.stop_time = stop
        return "updated"

    async def replace_breaks(self, plant_id: int, carousal_id: int, breaks: list[tuple[time, time]]) -> str:
        """Make the carousel's breaks equal to ``breaks``; returns ``unchanged`` or ``replaced``."""
        existing = (
            (
                await self._session.execute(
                    select(CarouselBreak)
                    .where(CarouselBreak.plant_id == plant_id, CarouselBreak.carousal_id == carousal_id)
                    .order_by(CarouselBreak.id)
                )
            )
            .scalars()
            .all()
        )
        if [(b.start_time, b.stop_time) for b in existing] == breaks:
            return "unchanged"
        await self._session.execute(
            delete(CarouselBreak).where(
                CarouselBreak.plant_id == plant_id, CarouselBreak.carousal_id == carousal_id
            )
        )
        for start, stop in breaks:
            self._session.add(
                CarouselBreak(plant_id=plant_id, carousal_id=carousal_id, start_time=start, stop_time=stop)
            )
        await self._session.flush()
        return "replaced"
