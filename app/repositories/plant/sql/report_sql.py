"""Dynamic SQL builders for the V2 reports (agent.md §5.7.3, §5.7.4, §8.4.3).

Phase windows come from configuration, so the statement *structure* depends on how many
windows a carousel has. The builders only interpolate generated placeholder names
(``:w0_from``, ``:b1_to``, ...); every time value is validated against ``TIME_PATTERN`` and
emitted as a bind parameter, never inline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import TextClause, bindparam, text

TIME_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d{1,3})?$")
ZERO_INTERVAL = "'0 seconds'::interval"

Window = dict[str, str]


@dataclass(frozen=True, slots=True)
class BuiltQuery:
    sql: TextClause
    time_params: dict[str, str]


def _validate(windows: Sequence[Window]) -> None:
    for w in windows:
        for key in ("from", "to"):
            if not TIME_PATTERN.match(w[key]):
                raise ValueError(f"invalid time value for phase window: {w[key]!r}")


def _time_params(prefix: str, windows: Sequence[Window]) -> dict[str, str]:
    params: dict[str, str] = {}
    for i, w in enumerate(windows):
        params[f"{prefix}{i}_from"] = w["from"]
        params[f"{prefix}{i}_to"] = w["to"]
    return params


def _inside(column: str, prefix: str, windows: Sequence[Window]) -> str:
    if not windows:
        return "FALSE"
    return " OR ".join(
        f"({column} BETWEEN CAST(:{prefix}{i}_from AS time) AND CAST(:{prefix}{i}_to AS time))"
        for i in range(len(windows))
    )


def _gap_sum(prefix: str, windows: Sequence[Window]) -> str:
    if not windows:
        return ZERO_INTERVAL
    return " + ".join(
        f"getGapBetweenTimes(process_time, prev_process_time, "
        f"CAST(:{prefix}{i}_from AS text), CAST(:{prefix}{i}_to AS text))"
        for i in range(len(windows))
    )


def _end_gap_sum(prefix: str, windows: Sequence[Window]) -> str:
    if not windows:
        return ZERO_INTERVAL
    return " + ".join(
        f"getEndGapForPhase(last_cyl_time, CAST(:{prefix}{i}_from AS text), CAST(:{prefix}{i}_to AS text))"
        for i in range(len(windows))
    )


def _both_in_same_window(prefix: str, windows: Sequence[Window]) -> str:
    if not windows:
        return "FALSE"
    return " OR ".join(
        f"(process_time::time BETWEEN CAST(:{prefix}{i}_from AS time) AND CAST(:{prefix}{i}_to AS time)"
        f" AND prev_process_time::time BETWEEN CAST(:{prefix}{i}_from AS time)"
        f" AND CAST(:{prefix}{i}_to AS time))"
        for i in range(len(windows))
    )


def build_phase_production_query(working: Sequence[Window], breaks: Sequence[Window]) -> BuiltQuery:
    """§5.7.3: classify rows into normal / break / overtime and count cylinder types."""
    _validate(working)
    _validate(breaks)
    normal = _inside("process_date::time", "w", working)
    brk = _inside("process_date::time", "b", breaks)
    sql = text(
        f"""
SELECT CASE WHEN {normal} THEN 'normal'
            WHEN {brk} THEN 'break'
            ELSE 'overtime' END AS phase,
       SUM(CASE WHEN cyl_type = 1 THEN 1 ELSE 0 END) AS prod_14_2,
       SUM(CASE WHEN cyl_type = 2 THEN 1 ELSE 0 END) AS prod_19
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id = :carousel
  AND process_status NOT IN :excluded
GROUP BY 1
"""
    ).bindparams(bindparam("process_ids", expanding=True), bindparam("excluded", expanding=True))
    return BuiltQuery(sql, {**_time_params("w", working), **_time_params("b", breaks)})


def build_gap_query(
    working: Sequence[Window], breaks: Sequence[Window], overtime: Sequence[Window]
) -> BuiltQuery:
    """§5.7.4 ``buildProductionGapQuery``: gap hours per phase for one carousel."""
    for group in (working, breaks, overtime):
        _validate(group)
    overtime_gap = (
        f"CASE WHEN prev_process_time IS NOT NULL AND ({_both_in_same_window('o', overtime)}) "
        f"THEN process_time::time - prev_process_time::time ELSE {ZERO_INTERVAL} END"
    )
    sql = text(
        f"""
WITH day_wise_data AS (
    SELECT system_id,
           process_date::date AS process_day,
           to_char(process_date, 'HH24:MI:SS.MS') AS process_time
    FROM production_log
    WHERE process_date BETWEEN :from_dt AND :to_dt
      AND process_id IN :process_ids
      AND cyl_type IN :cyl_types
      AND system_id = :carousel
),
time_gaps AS (
    SELECT system_id, process_day, process_time,
           LAG(process_time) OVER (
               PARTITION BY system_id, process_day ORDER BY process_time
           ) AS prev_process_time
    FROM day_wise_data
),
grouped_gaps AS (
    SELECT process_day, process_time, prev_process_time,
           {overtime_gap} AS overtime_gap,
           ({_gap_sum("b", breaks)}) AS break_gap,
           ({_gap_sum("w", working)}) AS normal_gap
    FROM time_gaps
),
last_cyl_data AS (
    SELECT process_day, MAX(process_time::time) AS last_cyl_time
    FROM day_wise_data
    GROUP BY process_day
),
end_gap_data AS (
    SELECT process_day,
           ({_end_gap_sum("w", working)}) AS normal_end_gap,
           ({_end_gap_sum("b", breaks)}) AS break_end_gap
    FROM last_cyl_data
),
intervening_gaps_data AS (
    SELECT d.process_day,
           COALESCE(SUM(g.break_gap), {ZERO_INTERVAL}) AS total_break_gap,
           COALESCE(SUM(g.normal_gap), {ZERO_INTERVAL}) AS total_normal_gap,
           COALESCE(SUM(g.overtime_gap), {ZERO_INTERVAL}) AS total_overtime_gap
    FROM (SELECT DISTINCT process_day FROM day_wise_data) d
    LEFT JOIN grouped_gaps g
           ON g.process_day = d.process_day
          AND g.break_gap + g.normal_gap + g.overtime_gap > '60 seconds'::interval
    GROUP BY d.process_day
)
SELECT EXTRACT(EPOCH FROM SUM(i.total_normal_gap + e.normal_end_gap)) / 3600 AS total_normal_gap,
       EXTRACT(EPOCH FROM SUM(i.total_break_gap + e.break_end_gap)) / 3600 AS total_break_gap,
       EXTRACT(EPOCH FROM SUM(i.total_overtime_gap)) / 3600 AS total_overtime_gap
FROM intervening_gaps_data i
JOIN end_gap_data e ON e.process_day = i.process_day
"""
    ).bindparams(bindparam("process_ids", expanding=True), bindparam("cyl_types", expanding=True))
    params: dict[str, Any] = {
        **_time_params("w", working),
        **_time_params("b", breaks),
        **_time_params("o", overtime),
    }
    return BuiltQuery(sql, params)


# ---- fixed statements ------------------------------------------------------------------

SQL_BOTTLING = text(
    """
SELECT system_id,
       SUM(CASE WHEN cyl_type = 1 THEN 1 ELSE 0 END) AS production_14_2,
       SUM(CASE WHEN cyl_type = 2 THEN 1 ELSE 0 END) AS production_19
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
  AND cyl_type IN :cyl_types
  AND process_status NOT IN :excluded
GROUP BY system_id
ORDER BY system_id
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("carousels", expanding=True),
    bindparam("cyl_types", expanding=True),
    bindparam("excluded", expanding=True),
)

SQL_FIRST_LAST = text(
    """
SELECT system_id, MAX(process_date) AS last_cyl_time, MIN(process_date) AS first_cyl_time
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
  AND cyl_type IN :cyl_types
GROUP BY system_id
ORDER BY system_id
"""
).bindparams(
    bindparam("process_ids", expanding=True),
    bindparam("carousels", expanding=True),
    bindparam("cyl_types", expanding=True),
)

# Ported verbatim, including LAG() OVER () without ORDER BY (D-22). The threshold is bound.
SQL_V1_GAP_HOURS = text(
    """
SELECT sum(gap)/60/60 AS total_gap FROM (
  WITH cte AS (
    SELECT id, production_log_id, LAG(process_date) OVER() AS from_time, process_date AS to_time,
           EXTRACT(EPOCH FROM (process_date - LAG(process_date) OVER())) AS gap
    FROM (SELECT (ROW_NUMBER() OVER (ORDER BY process_date) - 1) + 1 AS id, *
          FROM production_log
          WHERE process_date BETWEEN :from_dt AND :to_dt AND system_id = :carousel
            AND process_id IN :process_ids AND cyl_type IN :cyl_types
          ORDER BY process_date ASC) AS p_log
    ORDER BY production_log_id ASC)
  SELECT * FROM cte WHERE gap > :threshold_s) AS gap_data
"""
).bindparams(bindparam("process_ids", expanding=True), bindparam("cyl_types", expanding=True))

SQL_CS_STATUS_COUNTS = text(
    """
SELECT process_status, system_id, COUNT(*) AS cnt
FROM production_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
GROUP BY process_status, system_id
"""
).bindparams(bindparam("process_ids", expanding=True), bindparam("carousels", expanding=True))

SQL_GDPT_COUNTS = text(
    """
SELECT system_id,
       SUM(CASE WHEN process_status = 0 THEN 1 ELSE 0 END) AS accepted,
       SUM(CASE WHEN process_status <> 0 THEN 1 ELSE 0 END) AS rejected,
       COUNT(*) AS total
FROM event_log
WHERE process_date BETWEEN :from_dt AND :to_dt
  AND process_id IN :process_ids
  AND system_id IN :carousels
GROUP BY system_id
ORDER BY system_id
"""
).bindparams(bindparam("process_ids", expanding=True), bindparam("carousels", expanding=True))

SQL_NON_OPERATING_DAYS = text(
    """
SELECT COUNT(*) AS non_op_days
FROM generate_series(CAST(:from_date AS date), CAST(:to_date AS date), interval '1 day') AS d(day)
WHERE NOT EXISTS (
    SELECT 1 FROM production_log p
    WHERE p.process_date::date = d.day::date
      AND p.system_id = :carousel
      AND p.process_id IN :process_ids
      AND p.process_status NOT IN :excluded_status
)
"""
).bindparams(bindparam("process_ids", expanding=True), bindparam("excluded_status", expanding=True))

SQL_OT_PRODUCTION_PERIOD = text(
    """
SELECT part, EXTRACT(EPOCH FROM SUM(span)) / 3600 AS hours
FROM (
    SELECT 'pre' AS part, process_date::date AS process_day,
           MAX(process_date::time) - MIN(process_date::time) AS span
    FROM production_log
    WHERE process_date BETWEEN :from_dt AND :to_dt
      AND system_id = :carousel AND process_id IN :process_ids AND cyl_type IN :cyl_types
      AND process_date::time BETWEEN CAST(:day_start AS time) AND CAST(:shift_start AS time)
    GROUP BY process_date::date
    UNION ALL
    SELECT 'post' AS part, process_date::date AS process_day,
           MAX(process_date::time) - MIN(process_date::time) AS span
    FROM production_log
    WHERE process_date BETWEEN :from_dt AND :to_dt
      AND system_id = :carousel AND process_id IN :process_ids AND cyl_type IN :cyl_types
      AND process_date::time BETWEEN CAST(:shift_end AS time) AND CAST(:day_end AS time)
    GROUP BY process_date::date
) s
GROUP BY part
"""
).bindparams(bindparam("process_ids", expanding=True), bindparam("cyl_types", expanding=True))
