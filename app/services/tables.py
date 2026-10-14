"""Table endpoints: filling statistics, accuracy trend, periodic production (agent.md §5.5.5, §5.5.6, §5.6)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.timeutils import format_dt, ms_to_local
from app.repositories.plant.production_log_repo import ProductionLogRepository
from app.services.plant_access import PlantAccess

DEFAULT_INTERVAL_MIN = 60


class TableService:
    """Tabular plant data for one plant."""

    def __init__(self, access: PlantAccess) -> None:
        self._access = access

    async def filling_statistics(
        self, plant: str, from_ms: int, to_ms: int, carousal: int, cyl_type: int
    ) -> list[dict[str, Any]]:
        """§5.5.5: per-scale variation histogram, mean and population std-dev.

        Always grouped by ``device_id`` (never machine_id). An empty list means no data;
        the legacy adapter turns that into ``{status: failure, errors: null}`` (D-18).
        """
        async with self._access.session(plant) as session:
            rows = await ProductionLogRepository(session).filling_statistics(
                ms_to_local(from_ms), ms_to_local(to_ms), carousal, cyl_type
            )
        return [
            {
                "scaleId": r.scale_id,
                "n200": r.n200,
                "n150": r.n150,
                "n100": r.n100,
                "n50": r.n50,
                "nilVar": r.nil_var,
                "p50": r.p50,
                "p100": r.p100,
                "p150": r.p150,
                "p200": r.p200,
                "total": r.total,
                "average": r.average,
                "stdDev": r.std_dev,
            }
            for r in rows
        ]

    async def accuracy_trend(
        self, plant: str, from_dt: str, to_dt: str, carousal: int, cyl_type: int
    ) -> list[dict[str, Any]]:
        """§5.5.6: 15-minute buckets of the share of zero-variation fills.

        ``from_dt`` / ``to_dt`` are datetime strings passed through to the query as bound text.
        """
        async with self._access.session(plant) as session:
            rows = await ProductionLogRepository(session).accuracy_trend(from_dt, to_dt, carousal, cyl_type)
        return [{**r, "interval_start": format_dt(r["interval_start"])} for r in rows]

    async def periodic_production(
        self, plant: str, from_ms: int, to_ms: int, interval_min: int = DEFAULT_INTERVAL_MIN
    ) -> list[dict[str, Any]]:
        """§5.6: production per period and carousel/cylinder type.

        Bucketing uses integer division of the minute by ``interval_min`` (so intervals over
        60 collapse to hourly starts) while ``to`` is start + ``interval_min``, as in legacy.
        """
        from_dt, to_dt = ms_to_local(from_ms), ms_to_local(to_ms)
        show_date = from_dt.date() != to_dt.date()
        fmt = "%Y-%m-%d %H:%M" if show_date else "%H:%M"
        async with self._access.session(plant) as session:
            rows = await ProductionLogRepository(session).periodic_production(from_dt, to_dt, interval_min)
        return [
            {
                "from": r.period_start.strftime(fmt),
                "to": (r.period_start + timedelta(minutes=interval_min)).strftime(fmt),
                "c1_t1": r.c1_t1,
                "c1_t2": r.c1_t2,
                "c1_total": r.c1_t1 + r.c1_t2,
                "c2_t1": r.c2_t1,
                "c2_t2": r.c2_t2,
                "c2_total": r.c2_t1 + r.c2_t2,
            }
            for r in rows
        ]
