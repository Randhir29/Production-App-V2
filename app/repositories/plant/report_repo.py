"""Plant-DB queries for the performance reports (agent.md §5.7). Read-only, bound parameters."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.constants import (
    CHECK_SCALE_PROCESS_IDS,
    DEFAULT_CYL_TYPES,
    GD_PROCESS_IDS,
    MIN_INTERRUPTION_SECONDS,
    NON_OP_DAY_EXCLUDED_STATUS,
    NON_OP_DAY_PROCESS_IDS,
    PT_PROCESS_IDS,
    TARE_EXCLUDED,
)
from app.domain.phases import OVERTIME_DAY_END, OVERTIME_DAY_START
from app.domain.report_calcs import BottlingRow, CylTimeRow
from app.repositories.plant.sql.report_sql import (
    SQL_BOTTLING,
    SQL_CS_STATUS_COUNTS,
    SQL_FIRST_LAST,
    SQL_GDPT_COUNTS,
    SQL_NON_OPERATING_DAYS,
    SQL_OT_PRODUCTION_PERIOD,
    SQL_V1_GAP_HOURS,
    Window,
    build_gap_query,
    build_phase_production_query,
)


def _window(from_dt: datetime, to_dt: datetime) -> dict[str, Any]:
    return {"from_dt": from_dt, "to_dt": to_dt}


class ReportRepository:
    """Report queries over ``production_log`` and ``event_log`` of one plant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bottling_rows(
        self, from_dt: datetime, to_dt: datetime, carousels: Sequence[int]
    ) -> list[BottlingRow]:
        result = await self._session.execute(
            SQL_BOTTLING,
            {
                **_window(from_dt, to_dt),
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "carousels": list(carousels),
                "cyl_types": list(DEFAULT_CYL_TYPES),
                "excluded": list(TARE_EXCLUDED),
            },
        )
        return [BottlingRow(int(r.system_id), int(r.production_14_2), int(r.production_19)) for r in result]

    async def cyl_times(
        self, from_dt: datetime, to_dt: datetime, carousels: Sequence[int]
    ) -> list[CylTimeRow]:
        result = await self._session.execute(
            SQL_FIRST_LAST,
            {
                **_window(from_dt, to_dt),
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "carousels": list(carousels),
                "cyl_types": list(DEFAULT_CYL_TYPES),
            },
        )
        return [CylTimeRow(int(r.system_id), r.last_cyl_time, r.first_cyl_time) for r in result]

    async def v1_gap_hours(self, from_dt: datetime, to_dt: datetime, carousel: int) -> float | None:
        """D-22 query; ``None`` when the window has no gaps above the threshold."""
        result = await self._session.execute(
            SQL_V1_GAP_HOURS,
            {
                **_window(from_dt, to_dt),
                "carousel": carousel,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "cyl_types": list(DEFAULT_CYL_TYPES),
                "threshold_s": MIN_INTERRUPTION_SECONDS,
            },
        )
        row = result.first()
        value = None if row is None else row.total_gap
        return None if value is None else float(value)

    async def cs_status_counts(
        self, from_dt: datetime, to_dt: datetime, carousels: Sequence[int]
    ) -> list[tuple[int, int, int]]:
        result = await self._session.execute(
            SQL_CS_STATUS_COUNTS,
            {
                **_window(from_dt, to_dt),
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "carousels": list(carousels),
            },
        )
        return [(int(r.process_status), int(r.system_id), int(r.cnt)) for r in result]

    async def gdpt_counts(
        self, from_dt: datetime, to_dt: datetime, carousels: Sequence[int], kind: str
    ) -> list[tuple[int, int, int, int]]:
        ids = GD_PROCESS_IDS if kind == "gd" else PT_PROCESS_IDS
        result = await self._session.execute(
            SQL_GDPT_COUNTS,
            {
                **_window(from_dt, to_dt),
                "process_ids": list(ids),
                "carousels": list(carousels),
            },
        )
        return [(int(r.system_id), int(r.accepted), int(r.rejected), int(r.total)) for r in result]

    async def phase_production(
        self,
        from_dt: datetime,
        to_dt: datetime,
        carousel: int,
        working: Sequence[Window],
        breaks: Sequence[Window],
    ) -> list[tuple[str, int, int]]:
        built = build_phase_production_query(working, breaks)
        result = await self._session.execute(
            built.sql,
            {
                **built.time_params,
                **_window(from_dt, to_dt),
                "carousel": carousel,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "excluded": list(TARE_EXCLUDED),
            },
        )
        return [(str(r.phase), int(r.prod_14_2), int(r.prod_19)) for r in result]

    async def gap_hours(
        self,
        from_dt: datetime,
        to_dt: datetime,
        carousel: int,
        working: Sequence[Window],
        breaks: Sequence[Window],
        overtime: Sequence[Window],
    ) -> dict[str, float | None]:
        built = build_gap_query(working, breaks, overtime)
        result = await self._session.execute(
            built.sql,
            {
                **built.time_params,
                **_window(from_dt, to_dt),
                "carousel": carousel,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "cyl_types": list(DEFAULT_CYL_TYPES),
            },
        )
        row = result.first()
        keys = ("total_normal_gap", "total_break_gap", "total_overtime_gap")
        if row is None:
            return dict.fromkeys(keys)
        return {k: (None if getattr(row, k) is None else float(getattr(row, k))) for k in keys}

    async def non_operating_days(self, from_date: date, to_date: date, carousel: int) -> int:
        result = await self._session.execute(
            SQL_NON_OPERATING_DAYS,
            {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "carousel": carousel,
                "process_ids": list(NON_OP_DAY_PROCESS_IDS),
                "excluded_status": list(NON_OP_DAY_EXCLUDED_STATUS),
            },
        )
        row = result.first()
        return 0 if row is None else int(row.non_op_days)

    async def ot_production_period(
        self, from_dt: datetime, to_dt: datetime, carousel: int, start: str, end: str
    ) -> dict[str, float | None]:
        result = await self._session.execute(
            SQL_OT_PRODUCTION_PERIOD,
            {
                **_window(from_dt, to_dt),
                "carousel": carousel,
                "process_ids": list(CHECK_SCALE_PROCESS_IDS),
                "cyl_types": list(DEFAULT_CYL_TYPES),
                "day_start": OVERTIME_DAY_START,
                "shift_start": start,
                "shift_end": end,
                "day_end": OVERTIME_DAY_END,
            },
        )
        hours = {str(r.part): (None if r.hours is None else float(r.hours)) for r in result}
        return {
            "total_pre_shift_time": hours.get("pre"),
            "total_post_shift_time": hours.get("post"),
        }
