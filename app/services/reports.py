"""Performance reports V1 and V2 (agent.md §5.7). No HTTP concerns."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any

from app.core.errors import PlantConfigMissing
from app.core.timeutils import ms_to_local
from app.domain.phases import (
    carousels_missing_times,
    carousels_without_breaks,
    config_to_phases,
    daily_op_hours,
)
from app.domain.rejections import cs_rejection_report, gdpt_report
from app.domain.report_calcs import (
    bottling_summary,
    combine_gap_totals,
    first_last_cyl_times,
    phase_production,
    production_hours_summary,
    productivity_v1,
    productivity_v2,
)
from app.repositories.plant.report_repo import ReportRepository
from app.services.carousel_config import CarouselConfigService
from app.services.plant_access import PlantAccess

logger = logging.getLogger("prodash.services.reports")

END_OF_DAY_DROP_MS = time(23, 59, 59)  # PHP drops the .999 in the string windows (§5.2)
END_OF_DAY_MS = time(23, 59, 59, 999_000)


class ReportService:
    """Builds the V1/V2 performance report payloads for one plant."""

    def __init__(self, access: PlantAccess, carousels: CarouselConfigService) -> None:
        self._access = access
        self._carousels = carousels

    async def _config(self, plant: str) -> dict[int, dict[str, Any]]:
        config = await self._carousels.get_config(plant)
        if not config:
            logger.warning("report requested for plant without carousel config plant=%s", plant)
            raise PlantConfigMissing(plant)
        return config

    async def performance_v1(self, plant: str, from_ms: int, to_ms: int) -> dict[str, Any]:
        """§5.7.7 V1: the ``data`` object of ``getPerformanceReport``."""
        config = await self._config(plant)
        carousels = list(config)
        from_dt, to_dt = ms_to_local(from_ms), ms_to_local(to_ms)
        async with self._access.session(plant) as session:
            repo = ReportRepository(session)
            data = await self._v1_blocks(repo, from_dt, to_dt, carousels)
        return {"carousals": carousels, **data}

    async def _v1_blocks(
        self, repo: ReportRepository, from_dt: datetime, to_dt: datetime, carousels: list[int]
    ) -> dict[str, Any]:
        bottling = bottling_summary(await repo.bottling_rows(from_dt, to_dt, carousels))
        first_last = first_last_cyl_times(await repo.cyl_times(from_dt, to_dt, carousels), carousels)
        gaps = {c: await repo.v1_gap_hours(from_dt, to_dt, c) for c in carousels}
        hours = production_hours_summary(gaps, first_last)
        cs = cs_rejection_report(await repo.cs_status_counts(from_dt, to_dt, carousels), carousels)
        gd = gdpt_report(await repo.gdpt_counts(from_dt, to_dt, carousels, "gd"))
        pt = gdpt_report(await repo.gdpt_counts(from_dt, to_dt, carousels, "pt"))
        warnings: list[str] = []
        productivity = productivity_v1(bottling, hours, warnings)
        for w in warnings:
            logger.warning(w)
        return {
            "bottlingData": bottling,
            "productionHoursData": hours,
            "checkScaleRejectionSummary": cs,
            "gdRejectionSummary": gd,
            "ptRejectionSummary": pt,
            "productivityData": productivity,
        }

    async def _v2_phase_blocks(
        self,
        plant: str,
        config: dict[int, dict[str, Any]],
        from_date: date,
        to_date: date,
        repo: ReportRepository,
    ) -> tuple[dict[int, Any], dict[int, Any] | bool, dict[int, Any]]:
        """Phase-wise bottling, §5.7.5 hours (or ``False`` if from > to) and V2 productivity."""
        phases = config_to_phases(config)
        daily = daily_op_hours(phases)
        from_dt = datetime.combine(from_date, time(0, 0, 0))
        to_dt = datetime.combine(to_date, END_OF_DAY_MS)

        bottling2: dict[int, Any] = {}
        for c, p in phases.items():
            bottling2[c] = phase_production(
                await repo.phase_production(from_dt, to_dt, c, p["working"], p["breaks"])
            )

        if from_date > to_date:
            return bottling2, False, {}
        hours2: dict[int, Any] = {}
        for c, p in phases.items():
            times = config[c]["times"]
            gaps = await repo.gap_hours(from_dt, to_dt, c, p["working"], p["breaks"], p["overtime"])
            non_op = await repo.non_operating_days(from_date, to_date, c)
            ot = await repo.ot_production_period(from_dt, to_dt, c, times["start"], times["end"])
            hours2[c] = combine_gap_totals(c, gaps, non_op, from_date, to_date, daily[c], ot)
        return bottling2, hours2, productivity_v2(bottling2, hours2)

    async def _prepare_v2(self, plant: str) -> dict[int, dict[str, Any]]:
        config = await self._config(plant)
        missing = carousels_missing_times(config)
        no_breaks = carousels_without_breaks(config)
        if missing or no_breaks:
            logger.warning(
                "V2 report guard (D-06) plant=%s missing_times=%s no_breaks=%s",
                plant,
                missing,
                no_breaks,
            )
            raise PlantConfigMissing(plant)
        await self._access.ensure_gap_functions(plant)
        return config

    async def performance_v2(self, plant: str, from_date: date, to_date: date) -> dict[str, Any]:
        """§5.7.7 V2: the ``data`` object of ``getPerformanceReportV2``.

        Raises ``PlantConfigMissing`` for carousels without shift times or breaks (D-06).
        """
        config = await self._prepare_v2(plant)
        carousels = list(config)
        from_dt = datetime.combine(from_date, time(0, 0, 0))
        to_dt = datetime.combine(to_date, END_OF_DAY_DROP_MS)
        async with self._access.session(plant) as session:
            repo = ReportRepository(session)
            v1 = await self._v1_blocks(repo, from_dt, to_dt, carousels)
            bottling2, hours2, productivity2 = await self._v2_phase_blocks(
                plant, config, from_date, to_date, repo
            )
        return {
            "carousals": carousels,
            "bottlingData": v1["bottlingData"],
            "bottlingData2": bottling2,
            "productionHoursData": v1["productionHoursData"],
            "productionHoursData2": hours2,
            "checkScaleRejectionSummary": v1["checkScaleRejectionSummary"],
            "gdRejectionSummary": v1["gdRejectionSummary"],
            "ptRejectionSummary": v1["ptRejectionSummary"],
            "productivityData": v1["productivityData"],
            "productivityData2": productivity2,
        }

    async def performance_v2_data(self, plant: str, from_date: date, to_date: date) -> dict[str, Any]:
        """``getPerformanceReportV2Data`` (mailer): V2 with the full carousel config map."""
        config = await self._prepare_v2(plant)
        carousels = list(config)
        from_dt = datetime.combine(from_date, time(0, 0, 0))
        to_dt = datetime.combine(to_date, END_OF_DAY_DROP_MS)
        async with self._access.session(plant) as session:
            repo = ReportRepository(session)
            cs = cs_rejection_report(await repo.cs_status_counts(from_dt, to_dt, carousels), carousels)
            gd = gdpt_report(await repo.gdpt_counts(from_dt, to_dt, carousels, "gd"))
            pt = gdpt_report(await repo.gdpt_counts(from_dt, to_dt, carousels, "pt"))
            bottling2, hours2, productivity2 = await self._v2_phase_blocks(
                plant, config, from_date, to_date, repo
            )
        return {
            "carousals": config,
            "bottlingData": bottling2,
            "productionHoursData": hours2,
            "checkScaleRejectionSummary": cs,
            "gdRejectionSummary": gd,
            "ptRejectionSummary": pt,
            "productivityData": productivity2,
        }
