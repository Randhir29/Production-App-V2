"""Mailer feed, E27 (agent.md §5.7.8)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core.errors import AppError
from app.domain.mail_rows import transform_rows
from app.repositories.central.plant_repo import PlantRepository
from app.services.reports import ReportService

logger = logging.getLogger("prodash.services.mailer")


class MailerService:
    """Flattened per-carousel performance rows for the external mail job."""

    def __init__(self, plants: PlantRepository, reports: ReportService) -> None:
        self._plants = plants
        self._reports = reports

    async def performance_rows(self, plant: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
        """One row per carousel of ``plant``; ``[]`` when the plant or its data is unavailable."""
        registry = await self._plants.list_registry()
        carousal_counts = {r.value: r.carousals_count for r in registry}
        zone_maps = {r.value: r.zone for r in registry}

        plant_row = await self._plants.get_config_by_short_name(plant)
        if plant_row is None:
            return []
        try:
            report = await self._reports.performance_v2_data(plant, from_date, to_date)
        except AppError as exc:
            logger.warning("mailer feed skipped plant=%s code=%s", plant, exc.code)
            return []
        return transform_rows(
            {"short_name": plant_row.value, "name": plant_row.text},
            report,
            carousal_counts,
            zone_maps,
        )
