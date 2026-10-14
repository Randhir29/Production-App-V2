"""Plant listings, E01-E03 (agent.md §5.8)."""

from __future__ import annotations

from typing import Any

from app.domain.static_config import StaticPlantConfig
from app.repositories.central.plant_repo import PlantRepository


class PlantService:
    """Static and registry plant lists plus the zone map."""

    def __init__(self, plants: PlantRepository, static: StaticPlantConfig) -> None:
        self._plants = plants
        self._static = static

    def list_static(self) -> list[dict[str, str]]:
        """``getPlants``: static plants sorted by key (PHP ``ksort``) as ``{value, text}``."""
        return [{"value": k, "text": self._static.plants[k]} for k in sorted(self._static.plants)]

    async def list_registry(self) -> list[dict[str, Any]]:
        """``getPlants2``: central DB plants as ``{value, text, zone, carousals_count}``."""
        rows = await self._plants.list_registry()
        return [
            {"value": r.value, "text": r.text, "zone": r.zone, "carousals_count": r.carousals_count}
            for r in rows
        ]

    async def zone_map(self) -> list[dict[str, Any]]:
        """``zone-maps``: ``[{zone, plant}]`` in registry order."""
        rows = await self._plants.list_registry()
        return [{"zone": r.zone, "plant": r.value} for r in rows]
