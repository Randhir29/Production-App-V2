"""Scale analytics: under-performing, inaccurate, efficiency table (agent.md §5.5.1-§5.5.4)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.cache import TTLCache
from app.core.timeutils import ms_to_local, resolve_scale_window, to_epoch_seconds
from app.domain.constants import DEFAULT_CAROUSELS
from app.domain.scales import (
    compute_accuracies,
    compute_efficiencies,
    efficiency_tag,
    pivot_efficiency_rows,
    sort_under_performing,
)
from app.domain.static_config import StaticPlantConfig
from app.repositories.plant.production_log_repo import ProductionLogRepository
from app.services.plant_access import PlantAccess

logger = logging.getLogger("prodash.services.scales")

DEFAULT_UNDER_PERFORMING_ROWS = 10
DEFAULT_INACCURATE_ROWS = 5


def carousels_for_filter(carousal: int) -> tuple[int, ...]:
    """§5.5.4: 0 -> [1, 2], 1 -> [1], 2 -> [2], anything else -> [1, 2]."""
    return {1: (1,), 2: (2,)}.get(carousal, DEFAULT_CAROUSELS)


class ScaleService:
    """Scale-level efficiency and accuracy for one plant."""

    def __init__(self, access: PlantAccess, static: StaticPlantConfig, cache: TTLCache | None = None) -> None:
        self._access = access
        self._static = static
        self._cache = cache

    async def _cached(self, key: tuple[Any, ...], compute: Callable[[], Awaitable[Any]]) -> Any:
        if self._cache is None:
            return await compute()
        return await self._cache.get_or_compute(key, compute)

    async def under_performing(
        self,
        plant: str,
        row_count: int = DEFAULT_UNDER_PERFORMING_ROWS,
        period_hours: int = 0,
        from_ts: int = 0,
        to_ts: int = 0,
        with_tags: bool = False,
    ) -> dict[str, Any]:
        """§5.5.2, optionally served from the response cache (dashboard polling, §7.9)."""
        key = ("under-performing", plant, row_count, period_hours, from_ts, to_ts, with_tags)
        result: dict[str, Any] = await self._cached(
            key,
            lambda: self._under_performing(plant, row_count, period_hours, from_ts, to_ts, with_tags),
        )
        return result

    async def _under_performing(
        self,
        plant: str,
        row_count: int = DEFAULT_UNDER_PERFORMING_ROWS,
        period_hours: int = 0,
        from_ts: int = 0,
        to_ts: int = 0,
        with_tags: bool = False,
    ) -> dict[str, Any]:
        """§5.5.2 ``getUnderPerformingScales``: lowest-efficiency scales first."""
        from_dt, to_dt = resolve_scale_window(from_ts, to_ts, period_hours)
        window = to_epoch_seconds(to_dt) - to_epoch_seconds(from_dt)
        async with self._access.session(plant) as session:
            counts, spans = await ProductionLogRepository(session).scale_filling_counts(
                from_dt, to_dt, DEFAULT_CAROUSELS
            )
        result = compute_efficiencies(
            counts,
            spans,
            window,
            self._static.carousal_config.get(plant),
            self._static.carousal_speed,
            self._static.uses_device_id(plant),
        )
        for warning in result.warnings:
            logger.warning("%s plant=%s", warning, plant)
        rows = sort_under_performing(result.rows, row_count)
        if with_tags:
            rows = [{**r, "tag": efficiency_tag(r["efficiency"])} for r in rows]
        return {"rows": rows, "meta": result.meta}

    async def inaccurate(
        self,
        plant: str,
        row_count: int = DEFAULT_INACCURATE_ROWS,
        period_hours: int = 0,
        from_ts: int = 0,
        to_ts: int = 0,
    ) -> dict[str, Any]:
        """§5.5.3, optionally served from the response cache (dashboard polling, §7.9)."""
        key = ("inaccurate", plant, row_count, period_hours, from_ts, to_ts)
        result: dict[str, Any] = await self._cached(
            key, lambda: self._inaccurate(plant, row_count, period_hours, from_ts, to_ts)
        )
        return result

    async def _inaccurate(
        self,
        plant: str,
        row_count: int = DEFAULT_INACCURATE_ROWS,
        period_hours: int = 0,
        from_ts: int = 0,
        to_ts: int = 0,
    ) -> dict[str, Any]:
        """§5.5.3 ``getInaccurateScales``: lowest-accuracy scales first (D-01 formula)."""
        from_dt, to_dt = resolve_scale_window(from_ts, to_ts, period_hours)
        window = to_epoch_seconds(to_dt) - to_epoch_seconds(from_dt)
        async with self._access.session(plant) as session:
            rows = await ProductionLogRepository(session).inaccurate_scales(from_dt, to_dt)
        out, meta = compute_accuracies(rows, window, self._static.uses_device_id(plant), row_count)
        return {"rows": out, "meta": meta}

    async def efficiency_table(
        self, plant: str, from_ms: int, to_ms: int, carousal: int = 0
    ) -> dict[str, Any]:
        """§5.5.4 ``getScaleEfficiencyDetailsTableData2``: efficiency pivoted by scale."""
        from_dt, to_dt = ms_to_local(from_ms), ms_to_local(to_ms)
        window = to_epoch_seconds(to_dt) - to_epoch_seconds(from_dt)
        carousels = carousels_for_filter(carousal)
        async with self._access.session(plant) as session:
            counts, spans = await ProductionLogRepository(session).scale_filling_counts(
                from_dt, to_dt, carousels
            )
        result = compute_efficiencies(
            counts,
            spans,
            window,
            self._static.carousal_config.get(plant),
            self._static.carousal_speed,
            self._static.uses_device_id(plant),
            meta_carousels=carousels,
        )
        for warning in result.warnings:
            logger.warning("%s plant=%s", warning, plant)
        return {
            "rows": pivot_efficiency_rows(result.rows),
            "meta": result.meta,
            "carousals": int(carousal),
        }
