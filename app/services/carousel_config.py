"""CarouselConfigService (agent.md §4.1, §5.7.1). No HTTP concerns."""

from __future__ import annotations

import logging
from typing import Any

from app.core.errors import PlantConfigMissing, PlantNotFound
from app.domain.carousel_config import build_carousel_config, carousel_ids
from app.repositories.central.carousel_repo import CarouselRepository
from app.repositories.central.plant_repo import PlantRepository

logger = logging.getLogger("prodash.services.carousel_config")


class CarouselConfigService:
    """Derives the per-plant carousel configuration from the central DB tables."""

    def __init__(self, plants: PlantRepository, carousels: CarouselRepository) -> None:
        self._plants = plants
        self._carousels = carousels

    async def get_config(self, plant: str) -> dict[int, dict[str, Any]]:
        """Return ``{carousal_id: {heads, stdOutput, times: {start, end, breaks}}}`` (§4.1).

        An existing plant with no carousel rows yields ``{}``. Raises ``PlantNotFound`` for an
        unknown ``short_name``.
        """
        record = await self._plants.get_by_short_name(plant)
        if record is None:
            raise PlantNotFound(plant)
        carousels = await self._carousels.list_carousels(record.id)
        breaks = await self._carousels.list_breaks(record.id)
        return build_carousel_config(carousels, breaks)

    async def get_carousels(self, plant: str) -> list[int]:
        """Carousel ids used by the performance reports (§5.7.1).

        Raises ``PlantConfigMissing`` when the plant has no carousel rows (the legacy code
        produced broken SQL here).
        """
        config = await self.get_config(plant)
        if not config:
            logger.warning("plant has no carousel configuration plant=%s", plant)
            raise PlantConfigMissing(plant)
        return carousel_ids(config)
