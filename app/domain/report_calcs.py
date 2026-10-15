"""Report building blocks and productivity maths (agent.md §5.7.1, §5.7.5, §5.7.6). Pure."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.core.timeutils import to_epoch_seconds
from app.domain.constants import EQUIV_19KG

PHASES = ("normal", "break", "overtime")


@dataclass(frozen=True, slots=True)
class BottlingRow:
    system_id: int
    production_14_2: int
    production_19: int


@dataclass(frozen=True, slots=True)
class CylTimeRow:
    system_id: int
    last_cyl_time: datetime
    first_cyl_time: datetime


def pg_timestamp_text(value: datetime) -> str:
    """Render a timestamp the way PostgreSQL's text output does (fraction only when non-zero)."""
    text = value.strftime("%Y-%m-%d %H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}".rstrip("0")
    return text


def bottling_summary(rows: Sequence[BottlingRow]) -> dict[int, dict[str, int]] | None:
    """§5.7.1 ``bottling_summary``.

    ``None`` when there are no rows, or when the first row has both counts at 0 (D-25).
    """
    if not rows or (rows[0].production_14_2 == 0 and rows[0].production_19 == 0):
        return None
    return {
        r.system_id: {
            "carousal": r.system_id,
            "production_14_2": r.production_14_2,
            "production_19": r.production_19,
        }
        for r in rows
    }


def first_last_cyl_times(
    rows: Sequence[CylTimeRow], carousels: Iterable[int]
) -> dict[int, dict[str, Any]] | None:
    """§5.7.1 ``first_last_cyl_times``: ``{}`` for carousels without rows, ``None`` if no rows."""
    if not rows:
        return None
    by_carousel = {r.system_id: r for r in rows}
    result: dict[int, dict[str, Any]] = {}
    for carousel in carousels:
        r = by_carousel.get(carousel)
        if r is None:
            result[carousel] = {}
            continue
        result[carousel] = {
            "carousal": carousel,
            "last_cyl_time": pg_timestamp_text(r.last_cyl_time),
            "first_cyl_time": pg_timestamp_text(r.first_cyl_time),
            "total_hours": (to_epoch_seconds(r.last_cyl_time) - to_epoch_seconds(r.first_cyl_time)) / 3600,
        }
    return result


def production_hours_summary(
    gaps: Mapping[int, float | None], first_last: Mapping[int, dict[str, Any]] | None
) -> dict[int, dict[str, Any]]:
    """§5.7.1 V1 gaps: ``{carousel: {total_gap, carousal, **first_last}}``."""
    summary: dict[int, dict[str, Any]] = {}
    for carousel, total_gap in gaps.items():
        entry: dict[str, Any] = {"total_gap": total_gap, "carousal": carousel}
        if first_last and first_last.get(carousel):
            entry.update(first_last[carousel])
        summary[carousel] = entry
    return summary


def _zero_safe(numerator: float, denominator: float, warnings: list[str], what: str) -> float:
    if denominator == 0:
        warnings.append(f"division by zero guarded (D-05): {what}")
        return 0.0
    return numerator / denominator


def productivity_v1(
    bottling: Mapping[int, Mapping[str, Any]] | None,
    hours: Mapping[int, Mapping[str, Any]],
    warnings: list[str] | None = None,
) -> dict[int, dict[str, Any]]:
    """§5.7.6 ``calculateProductivity`` (V1). ``{}`` when bottling is ``None``."""
    sink = warnings if warnings is not None else []
    if not bottling:
        return {}
    result: dict[int, dict[str, Any]] = {}
    for carousel, row in bottling.items():
        total = row["production_14_2"] + EQUIV_19KG * row["production_19"]
        h = hours.get(carousel, {})
        net_hours = (h.get("total_hours") or 0) - (h.get("total_gap") or 0)
        result[carousel] = {
            "carousal": carousel,
            "total_production": total,
            "net_hours": net_hours,
            "productivity": _zero_safe(total, net_hours, sink, f"productivity c{carousel}"),
        }
    return result


def productivity_v2(
    bottling2: Mapping[int, Mapping[str, Mapping[str, Any]]],
    hours2: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """§5.7.6 ``calculateProductivityV2``: per carousel and phase.

    ``net_hours`` may be negative; ``productivity`` is 0 only when ``net_hours`` is 0.
    """
    result: dict[int, dict[str, Any]] = {}
    for carousel, phases in bottling2.items():
        h = hours2.get(carousel, {})
        entry: dict[str, Any] = {"carousal": carousel}
        for phase in PHASES:
            prod = phases.get(phase) or {}
            total = (prod.get("prod_14_2") or 0) + EQUIV_19KG * (prod.get("prod_19") or 0)
            gap = h.get(f"total_{phase}_gap") or 0
            if phase == "overtime":
                net = (h.get("total_pre_shift_time") or 0) + (h.get("total_post_shift_time") or 0) - gap
            else:
                net = (h.get("max_op_hours") or {}).get(phase, 0) - gap
            entry[phase] = {
                "total_production": total,
                "net_hours": net,
                "productivity": total / net if net else 0,
            }
        result[carousel] = entry
    return result


def combine_gap_totals(
    carousel: int,
    gaps: Mapping[str, float | None],
    non_op_days: int,
    from_date: date,
    to_date: date,
    daily_hours: Mapping[str, float],
    ot: Mapping[str, float | None],
) -> dict[str, Any]:
    """§5.7.5 ``production_gaps_for_all_carousels`` entry for one carousel."""
    intervening = (to_date - from_date).days + 1
    net_days = intervening - non_op_days
    max_op = {
        "normal": daily_hours["normal"] * net_days,
        "break": daily_hours["break"] * net_days,
    }
    total_normal = gaps.get("total_normal_gap")
    total_break = gaps.get("total_break_gap")
    return {
        "carousal": carousel,
        "total_normal_gap": total_normal,
        "total_break_gap": total_break,
        "total_overtime_gap": gaps.get("total_overtime_gap"),
        "intervening_days": intervening,
        "non_op_days": non_op_days,
        "net_op_days": net_days,
        "daily_op_hours": dict(daily_hours),
        "max_op_hours": max_op,
        "net_op_hours": {
            "normal": max_op["normal"] - (total_normal or 0),
            "break": max_op["break"] - (total_break or 0),
        },
        "total_pre_shift_time": ot.get("total_pre_shift_time"),
        "total_post_shift_time": ot.get("total_post_shift_time"),
    }


def phase_production(rows: Iterable[tuple[str, int, int]]) -> dict[str, dict[str, Any]]:
    """§5.7.3: ``{normal|break|overtime: {...}}``; phases without rows default to zeros."""
    result: dict[str, dict[str, Any]] = {phase: {"prod_14_2": 0, "prod_19": 0} for phase in PHASES}
    for phase, p14, p19 in rows:
        result[phase] = {"phase": phase, "prod_14_2": p14, "prod_19": p19}
    return result
