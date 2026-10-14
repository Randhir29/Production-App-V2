"""Central-DB carousel and break queries (agent.md §4.1, §7.4)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.carousel_config import BreakRow, CarouselRow, format_time
from app.models import Carousel, CarouselBreak


class CarouselRepository:
    """Reads ``carousals`` and ``breaks``; time values are normalised to ``"HH:MM:SS"``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_carousels(self, plant_id: int) -> list[CarouselRow]:
        """Carousels of a plant in table order (``ORDER BY id``)."""
        stmt = select(Carousel).where(Carousel.plant_id == plant_id).order_by(Carousel.id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            CarouselRow(
                carousal_id=int(r.carousal_id),
                heads=int(r.heads),
                rated_productivity=int(r.rated_productivity),
                start_time=format_time(r.start_time),
                stop_time=format_time(r.stop_time),
            )
            for r in rows
        ]

    async def list_breaks(self, plant_id: int) -> list[BreakRow]:
        """Breaks of a plant in table order (``ORDER BY id``), all carousels."""
        stmt = select(CarouselBreak).where(CarouselBreak.plant_id == plant_id).order_by(CarouselBreak.id)
        rows = (await self._session.execute(stmt)).scalars().all()
        result: list[BreakRow] = []
        for r in rows:
            start, stop = format_time(r.start_time), format_time(r.stop_time)
            if start is None or stop is None:
                continue
            result.append(BreakRow(int(r.carousal_id), start, stop))
        return result
