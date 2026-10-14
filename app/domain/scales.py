"""Scale efficiency / accuracy maths (agent.md §5.5.1-§5.5.4). Pure, no I/O.

Divisions that crashed the PHP 8 code (D-05) are guarded: the value becomes 0 and a warning
string is returned for the caller to log.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.timeutils import to_epoch_seconds
from app.domain.php_compat import php_usort
from app.domain.static_config import StaticCarousel

UNCONFIGURED_HEADS = 24  # §5.5.2: head count assumed for plants without static config

EFFICIENCY_SORT_FACTOR = 100  # D-15
ACCURACY_SORT_FACTOR = 10_000  # D-15

EFFICIENCY_BELOW_AVERAGE = 0.75
EFFICIENCY_AVERAGE = 1.0


@dataclass(frozen=True, slots=True)
class ScaleCount:
    """One row of ``getScalesFillingCount``."""

    machine_id: int
    system_id: int
    device_id: int
    count: int


@dataclass(frozen=True, slots=True)
class CarouselSpan:
    """``metaData[c]``: first and last accepted cylinder time of a carousel."""

    first_cyl_time: datetime
    last_cyl_time: datetime


@dataclass(frozen=True, slots=True)
class AccuracyCount:
    """One row of the inaccurate-scales variation query."""

    machine_id: int
    system_id: int
    device_id: int
    total: int
    hundred_plus: int


@dataclass(slots=True)
class EfficiencyResult:
    rows: list[dict[str, Any]]
    meta: dict[str, float]
    std_output: dict[int, float] = field(default_factory=dict)
    interval: dict[int, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def scale_id(machine_id: int, device_id: int, uses_device_id: bool) -> int:
    """§5.1 scale identifier rule."""
    return device_id if uses_device_id else machine_id


def _div(numerator: float, denominator: float, warnings: list[str], what: str) -> float:
    if denominator == 0:
        warnings.append(f"division by zero guarded (D-05): {what}")
        return 0.0
    return numerator / denominator


def compute_efficiencies(
    counts: Sequence[ScaleCount],
    spans: Mapping[int, CarouselSpan],
    window_seconds: int,
    static_carousels: Mapping[int, StaticCarousel] | None,
    carousal_speed: Mapping[int, int],
    uses_device_id: bool,
    meta_carousels: Iterable[int] = (1, 2),
) -> EfficiencyResult:
    """§5.5.2 ``getScalesEfficiencies``.

    ``static_carousels`` is the plant's static ``carousal_config`` (``None`` when the plant is
    not configured there). ``window_seconds`` is ``to - from``.
    """
    meta: dict[str, float] = {"car1Eff": 0, "car2Eff": 0}
    if not counts:
        return EfficiencyResult(rows=[], meta=meta)

    result = EfficiencyResult(rows=[], meta=meta)
    for carousel, span in spans.items():
        span_seconds = to_epoch_seconds(span.last_cyl_time) - to_epoch_seconds(span.first_cyl_time)
        result.interval[carousel] = min(window_seconds, span_seconds)

    for carousel, interval in result.interval.items():
        if static_carousels is not None:
            cfg = static_carousels.get(carousel)
            if cfg is None:
                result.std_output[carousel] = 0.0
            else:
                result.std_output[carousel] = _div(
                    cfg.stdOutput * (interval / 3600), cfg.heads, result.warnings, f"heads c{carousel}"
                )
        else:
            speed = carousal_speed.get(carousel, 0)
            result.std_output[carousel] = _div(interval, speed, result.warnings, f"speed c{carousel}")

    totals: dict[int, int] = {}
    for row in counts:
        std = result.std_output.get(row.system_id, 0.0)
        totals[row.system_id] = totals.get(row.system_id, 0) + row.count
        result.rows.append(
            {
                "system_id": row.system_id,
                "machine_id": row.machine_id,
                "device_id": row.device_id,
                "count": row.count,
                "scale": scale_id(row.machine_id, row.device_id, uses_device_id),
                "carousal": row.system_id,
                "efficiency": _div(row.count, std, result.warnings, f"efficiency c{row.system_id}"),
                "stdOutput": std,
                "interval": result.interval.get(row.system_id, 0),
            }
        )

    for carousel in meta_carousels:
        total = totals.get(carousel, 0)
        std = result.std_output.get(carousel, 0.0)
        if static_carousels is not None:
            cfg = static_carousels.get(carousel)
            value = 0.0 if cfg is None else _div(total, std * cfg.heads, result.warnings, f"car{carousel}Eff")
        else:
            value = _div(total, std * UNCONFIGURED_HEADS, result.warnings, f"car{carousel}Eff")
        meta[f"car{carousel}Eff"] = value
    return result


def sort_under_performing(rows: Sequence[dict[str, Any]], row_count: int) -> list[dict[str, Any]]:
    """Ascending by efficiency with the truncating comparator (D-15), first ``row_count`` rows."""
    return php_usort(rows, lambda r: r["efficiency"], EFFICIENCY_SORT_FACTOR)[:row_count]


def compute_accuracies(
    rows: Sequence[AccuracyCount], window_seconds: int, uses_device_id: bool, row_count: int
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """§5.5.3: per-scale accuracy, sorted ascending, plus per-carousel meta."""
    meta: dict[str, float] = {"car1Acc": 0, "car2Acc": 0}
    if not rows:
        return [], meta

    out: list[dict[str, Any]] = []
    sums: dict[int, list[int]] = {}
    for r in rows:
        acc = sums.setdefault(r.system_id, [0, 0])
        acc[0] += r.hundred_plus
        acc[1] += r.total
        out.append(
            {
                "machine_id": r.machine_id,
                "system_id": r.system_id,
                "device_id": r.device_id,
                "total": r.total,
                "hundred_plus": r.hundred_plus,
                "accuracy": 1 - r.hundred_plus / r.total if r.total else 0.0,
                "scale": scale_id(r.machine_id, r.device_id, uses_device_id),
                "carousal": r.system_id,
                "interval": window_seconds,
            }
        )
    for carousel in (1, 2):
        hundred_plus, total = sums.get(carousel, [0, 0])
        meta[f"car{carousel}Acc"] = 1 - hundred_plus / max(total, 1)
    ordered = php_usort(out, lambda r: r["accuracy"], ACCURACY_SORT_FACTOR)
    return ordered[:row_count], meta


def pivot_efficiency_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """§5.5.4: one row per scale in first-seen order; carousel keys only where data exists."""
    pivot: dict[int, dict[str, Any]] = {}
    for r in rows:
        entry = pivot.setdefault(int(r["scale"]), {"scaleId": int(r["scale"])})
        c = r["carousal"]
        entry[f"car{c}Count"] = r["count"]
        entry[f"car{c}Percent"] = r["efficiency"]
    return list(pivot.values())


def efficiency_tag(efficiency: float) -> str:
    """Frontend tag thresholds (§5.5.2)."""
    if efficiency <= EFFICIENCY_BELOW_AVERAGE:
        return "below-average"
    if efficiency <= EFFICIENCY_AVERAGE:
        return "average"
    return "above-average"
