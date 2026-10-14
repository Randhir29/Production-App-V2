"""Central-DB plant queries (agent.md §5.8, §7.4). SQL only; no business maths."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Carousel, Plant


@dataclass(frozen=True, slots=True)
class PlantRecord:
    """A ``plants`` row. The password is excluded from ``repr`` so it never reaches logs."""

    id: int
    short_name: str
    plant_name: str | None
    zone: str | None
    host_ip: str | None
    db_user: str | None
    db_password: str | None = field(repr=False)
    db_database: str | None


@dataclass(frozen=True, slots=True)
class PlantRegistryRow:
    """One ``getPlants2`` row: ``{value, text, zone, carousals_count}``."""

    value: str
    text: str | None
    zone: str | None
    carousals_count: int


class PlantRepository:
    """Reads the ``plants`` table (and its carousel counts) from the central DB."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_short_name(self, short_name: str) -> PlantRecord | None:
        """Return the plant with this ``short_name``; the lowest id if the name is duplicated."""
        stmt = select(Plant).where(Plant.short_name == short_name).order_by(Plant.id).limit(1)
        plant = (await self._session.execute(stmt)).scalar_one_or_none()
        if plant is None:
            return None
        return PlantRecord(
            id=plant.id,
            short_name=plant.short_name,
            plant_name=plant.plant_name,
            zone=plant.zone,
            host_ip=plant.host_ip,
            db_user=plant.db_user,
            db_password=plant.db_password,
            db_database=plant.db_database,
        )

    def _registry_stmt(self) -> Select[str, str | None, str | None, int]:
        # Every non-aggregated column is in GROUP BY so the query is valid on PostgreSQL (D-27).
        return (
            select(
                Plant.short_name,
                Plant.plant_name,
                Plant.zone,
                func.count(Carousel.carousal_id).label("carousals_count"),
            )
            .select_from(Plant)
            .outerjoin(Carousel, Plant.id == Carousel.plant_id)
            .group_by(Plant.id, Plant.short_name, Plant.plant_name, Plant.zone)
            .order_by(Plant.short_name)
        )

    async def list_registry(self) -> list[PlantRegistryRow]:
        """All plants with their carousel counts, ordered by ``short_name`` (``getPlants2``)."""
        result = await self._session.execute(self._registry_stmt())
        return [PlantRegistryRow(r.short_name, r.plant_name, r.zone, int(r.carousals_count)) for r in result]

    async def get_config_by_short_name(self, short_name: str) -> PlantRegistryRow | None:
        """``getPlantsConfigByShortName``: the registry row, or ``None`` unless exactly one matches."""
        stmt = self._registry_stmt().where(Plant.short_name == short_name)
        rows = (await self._session.execute(stmt)).all()
        if len(rows) != 1:
            return None
        r = rows[0]
        return PlantRegistryRow(r.short_name, r.plant_name, r.zone, int(r.carousals_count))
