"""``production_log`` queries for scales and tables (agent.md §5.5, §5.6, §8.4).

SQL structure is ported from the legacy model; every value is a bound parameter (D-07) and the
status/process-id tuples come from ``domain.constants``. Plant connections are read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.constants import (
    ACCURACY_TREND_BUCKET_MIN,
    ACCURACY_TREND_RANGE_G,
    CHECK_SCALE_PROCESS_IDS,
    DEFAULT_CYL_TYPES,
    FILL_ACCEPTED_STATUSES,
    FILLING_CLAMP_G,
    NOMINAL_NET_G,
    TARE_EXCLUDED,
)
from app.domain.scales import AccuracyCount, CarouselSpan, ScaleCount

HUNDRED_BAND_G = 100  # lower bound of the legacy hundred_plus bucket (§5.4.2)

SQL_SCALE_FILLING_COUNT = text(
    """
SELECT machine_id, system_id, device_id, COUNT(*) AS cnt
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
  AND cyl_type IN :cyl_types
  AND process_status NOT IN :excluded
GROUP BY machine_id, system_id, device_id
ORDER BY system_id, machine_id
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("carousels", expanding=True),
    bindparam("cyl_types", expanding=True),
    bindparam("excluded", expanding=True),
)

SQL_CAROUSEL_SPAN = text(
    """
SELECT system_id, MIN(process_date) AS first_cyl_time, MAX(process_date) AS last_cyl_time
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
  AND cyl_type IN :cyl_types
  AND process_status NOT IN :excluded
GROUP BY system_id
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("carousels", expanding=True),
    bindparam("cyl_types", expanding=True),
    bindparam("excluded", expanding=True),
)

# hundred_plus: legacy formula verbatim, including the 14200 bound used for 19 kg cylinders (D-01).
SQL_INACCURATE_SCALES = text(
    """
SELECT machine_id, system_id, device_id,
       COUNT(*) AS total,
       SUM(CASE
             WHEN cyl_type = 1
                  AND ABS(check_net - :nominal_1) > :band AND ABS(check_net - :nominal_1) <= :limit
               THEN 1
             WHEN cyl_type = 2
                  AND ((check_net - :nominal_2 > :band AND check_net - :nominal_1 <= :limit)
                    OR (check_net - :nominal_2 < -:band AND check_net - :nominal_1 >= -:limit))
               THEN 1
             ELSE 0
           END) AS hundred_plus
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
  AND cyl_type IN :cyl_types
GROUP BY machine_id, system_id, device_id
ORDER BY system_id, machine_id
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("carousels", expanding=True),
    bindparam("cyl_types", expanding=True),
)

SQL_FILLING_STATISTICS = text(
    """
SELECT device_id,
       COUNT(*) AS cnt,
       SUM(CASE WHEN d <= -:clamp THEN 1 ELSE 0 END) AS n200,
       SUM(CASE WHEN d = -150 THEN 1 ELSE 0 END) AS n150,
       SUM(CASE WHEN d = -100 THEN 1 ELSE 0 END) AS n100,
       SUM(CASE WHEN d = -50 THEN 1 ELSE 0 END) AS n50,
       SUM(CASE WHEN d = 0 THEN 1 ELSE 0 END) AS nil_var,
       SUM(CASE WHEN d = 50 THEN 1 ELSE 0 END) AS p50,
       SUM(CASE WHEN d = 100 THEN 1 ELSE 0 END) AS p100,
       SUM(CASE WHEN d = 150 THEN 1 ELSE 0 END) AS p150,
       SUM(CASE WHEN d >= :clamp THEN 1 ELSE 0 END) AS p200,
       AVG(GREATEST(-:clamp, LEAST(:clamp, d))) AS average,
       STDDEV_POP(GREATEST(-:clamp, LEAST(:clamp, d))) AS stddev
FROM (
    SELECT system_id, device_id, check_net - :nominal AS d
    FROM production_log
    WHERE process_date BETWEEN :from_dt AND :to_dt
      AND system_id = :carousel
      AND process_id IN :process_ids
      AND cyl_type = :cyl_type
      AND process_status IN :accepted
) v
GROUP BY system_id, device_id
ORDER BY device_id
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("accepted", expanding=True),
)

SQL_ACCURACY_TREND = text(
    """
SELECT :carousel AS carousal,
       date_trunc('minute', process_date)
         - (EXTRACT(MINUTE FROM process_date)::int % :bucket_min) * interval '1 minute'
         AS interval_start,
       SUM(CASE WHEN d = 0 THEN 1 ELSE 0 END) AS variation_eq_0,
       COUNT(*) AS total_cylinders,
       ROUND(SUM(CASE WHEN d = 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 2)
         AS variation_eq_0_percentage
FROM (
    SELECT process_date, check_net - :nominal AS d
    FROM production_log
    WHERE process_date BETWEEN CAST(:from_dt AS timestamp) AND CAST(:to_dt AS timestamp)
      AND system_id = :carousel
      AND process_id IN :process_ids
      AND cyl_type = :cyl_type
) v
WHERE d BETWEEN -:range AND :range
GROUP BY 2
ORDER BY 2
"""
).bindparams(bindparam("process_ids", expanding=True))

SQL_PERIODIC_PRODUCTION = text(
    """
SELECT date_trunc('hour', process_date)
         + ((EXTRACT(MINUTE FROM process_date)::int / :interval_min) * :interval_min)
           * interval '1 minute' AS period_start,
       SUM(CASE WHEN system_id = 1 AND cyl_type = 1 THEN 1 ELSE 0 END) AS c1_t1,
       SUM(CASE WHEN system_id = 1 AND cyl_type = 2 THEN 1 ELSE 0 END) AS c1_t2,
       SUM(CASE WHEN system_id = 2 AND cyl_type = 1 THEN 1 ELSE 0 END) AS c2_t1,
       SUM(CASE WHEN system_id = 2 AND cyl_type = 2 THEN 1 ELSE 0 END) AS c2_t2
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
  AND cyl_type IN :cyl_types
GROUP BY 1
ORDER BY 1
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("carousels", expanding=True),
    bindparam("cyl_types", expanding=True),
)


@dataclass(frozen=True, slots=True)
class FillingStatRow:
    scale_id: int
    n200: int
    n150: int
    n100: int
    n50: int
    nil_var: int
    p50: int
    p100: int
    p150: int
    p200: int
    total: int
    average: float
    std_dev: float


@dataclass(frozen=True, slots=True)
class PeriodCountRow:
    period_start: datetime
    c1_t1: int
    c1_t2: int
    c2_t1: int
    c2_t2: int


def _window(from_dt: datetime, to_dt: datetime) -> dict[str, Any]:
    return {"from_dt": from_dt, "to_dt": to_dt}


class ProductionLogRepository:
    """Read-only queries over a plant's ``production_log``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def scale_filling_counts(
        self, from_dt: datetime, to_dt: datetime, carousels: tuple[int, ...]
    ) -> tuple[list[ScaleCount], dict[int, CarouselSpan]]:
        """§5.5.1: per-scale counts and per-carousel first/last cylinder times."""
        params = {
            **_window(from_dt, to_dt),
            "process_ids": list(CHECK_SCALE_PROCESS_IDS),
            "carousels": list(carousels),
            "cyl_types": list(DEFAULT_CYL_TYPES),
            "excluded": list(TARE_EXCLUDED),
        }
        counts = [
            ScaleCount(int(r.machine_id), int(r.system_id), int(r.device_id), int(r.cnt))
            for r in await self._session.execute(SQL_SCALE_FILLING_COUNT, params)
        ]
        if not counts:
            return [], {}
        spans = {
            int(r.system_id): CarouselSpan(r.first_cyl_time, r.last_cyl_time)
            for r in await self._session.execute(SQL_CAROUSEL_SPAN, params)
        }
        return counts, spans

    async def inaccurate_scales(self, from_dt: datetime, to_dt: datetime) -> list[AccuracyCount]:
        """§5.5.3 variation query (all statuses, carousels 1 and 2, D-01 formula)."""
        params = {
            **_window(from_dt, to_dt),
            "process_ids": list(CHECK_SCALE_PROCESS_IDS),
            "carousels": [1, 2],
            "cyl_types": list(DEFAULT_CYL_TYPES),
            "nominal_1": NOMINAL_NET_G[1],
            "nominal_2": NOMINAL_NET_G[2],
            "band": HUNDRED_BAND_G,
            "limit": FILLING_CLAMP_G,
        }
        result = await self._session.execute(SQL_INACCURATE_SCALES, params)
        return [
            AccuracyCount(
                int(r.machine_id), int(r.system_id), int(r.device_id), int(r.total), int(r.hundred_plus)
            )
            for r in result
        ]

    async def filling_statistics(
        self, from_dt: datetime, to_dt: datetime, carousel: int, cyl_type: int
    ) -> list[FillingStatRow]:
        """§5.5.5 per-device variation histogram, mean and population std-dev."""
        result = await self._session.execute(
            SQL_FILLING_STATISTICS,
            {
                **_window(from_dt, to_dt),
                "carousel": carousel,
                "cyl_type": cyl_type,
                "nominal": NOMINAL_NET_G[cyl_type],
                "clamp": FILLING_CLAMP_G,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "accepted": list(FILL_ACCEPTED_STATUSES),
            },
        )
        return [
            FillingStatRow(
                scale_id=int(r.device_id),
                n200=int(r.n200),
                n150=int(r.n150),
                n100=int(r.n100),
                n50=int(r.n50),
                nil_var=int(r.nil_var),
                p50=int(r.p50),
                p100=int(r.p100),
                p150=int(r.p150),
                p200=int(r.p200),
                total=int(r.cnt),
                average=float(r.average),
                std_dev=float(r.stddev),
            )
            for r in result
        ]

    async def accuracy_trend(
        self, from_dt: str, to_dt: str, carousel: int, cyl_type: int
    ) -> list[dict[str, Any]]:
        """§5.5.6 15-minute buckets of zero-variation share (bounds passed through as text)."""
        result = await self._session.execute(
            SQL_ACCURACY_TREND,
            {
                "from_dt": from_dt,
                "to_dt": to_dt,
                "carousel": carousel,
                "cyl_type": cyl_type,
                "nominal": NOMINAL_NET_G[cyl_type],
                "range": ACCURACY_TREND_RANGE_G,
                "bucket_min": ACCURACY_TREND_BUCKET_MIN,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
            },
        )
        return [
            {
                "carousal": int(r.carousal),
                "interval_start": r.interval_start,
                "variation_eq_0": int(r.variation_eq_0),
                "total_cylinders": int(r.total_cylinders),
                "variation_eq_0_percentage": (
                    None if r.variation_eq_0_percentage is None else float(r.variation_eq_0_percentage)
                ),
            }
            for r in result
        ]

    async def periodic_production(
        self, from_dt: datetime, to_dt: datetime, interval_min: int
    ) -> list[PeriodCountRow]:
        """§5.6 counts per carousel and cylinder type; no status exclusion."""
        result = await self._session.execute(
            SQL_PERIODIC_PRODUCTION,
            {
                **_window(from_dt, to_dt),
                "interval_min": interval_min,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "carousels": [1, 2],
                "cyl_types": list(DEFAULT_CYL_TYPES),
            },
        )
        return [
            PeriodCountRow(r.period_start, int(r.c1_t1), int(r.c1_t2), int(r.c2_t1), int(r.c2_t2))
            for r in result
        ]
