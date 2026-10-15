"""Mailer feed row transformation (agent.md §5.7.8 step 4). Pure."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.php_compat import php_round
from app.domain.rejections import rejection_percent
from app.domain.report_calcs import PHASES


def transform_rows(
    plant: Mapping[str, Any],
    report: Mapping[str, Any],
    carousal_counts: Mapping[str, int],
    zone_maps: Mapping[str, str | None],
) -> list[dict[str, Any]]:
    """Flatten one plant's V2 mailer data into one row per carousel.

    ``report`` is the ``getPerformanceReportV2Data`` payload (``carousals`` is the full DB
    config map). ``productivityData`` holds the V2 per-phase results.
    """
    short = plant["short_name"]
    bottling = report.get("bottlingData") or {}
    hours = report.get("productionHoursData") or {}
    productivity = report.get("productivityData") or {}
    cs = report.get("checkScaleRejectionSummary") or {}
    gd = report.get("gdRejectionSummary") or {}
    pt = report.get("ptRejectionSummary") or {}

    rows: list[dict[str, Any]] = []
    for car_id, car in (report.get("carousals") or {}).items():
        phases = bottling.get(car_id) or {}
        p14 = sum((phases.get(ph) or {}).get("prod_14_2", 0) for ph in PHASES)
        p19 = sum((phases.get(ph) or {}).get("prod_19", 0) for ph in PHASES)
        prod = productivity.get(car_id) or {}
        normal = prod.get("normal") or {}
        brk = prod.get("break") or {}
        ot = prod.get("overtime") or {}
        rows.append(
            {
                "isAdditionalCarousel": car_id > 1,
                "short_name": short,
                "name": plant["name"],
                "zone": zone_maps.get(short),
                "carousel": car_id,
                "filling_heads": car["heads"],
                "carousel_count": carousal_counts.get(short),
                "bottling": {"14_2kg": p14, "19kg": p19, "total": p14 + p19},
                "productivity": {
                    "normal": {
                        "production": normal.get("total_production", 0),
                        "stoppages": php_round((hours.get(car_id) or {}).get("total_normal_gap") or 0, 2),
                        "productivity": php_round(normal.get("productivity", 0), 0),
                    },
                    "break": {
                        "production": brk.get("total_production", 0),
                        "net_hours": php_round(brk.get("net_hours", 0), 2),
                        "productivity": php_round(brk.get("productivity", 0), 0),
                    },
                    "overtime": {
                        "production": ot.get("total_production", 0),
                        "net_hours": php_round(ot.get("net_hours", 0), 2),
                        "productivity": php_round(ot.get("productivity", 0), 0),
                    },
                },
                "rejections": {
                    "eld": {"percent": php_round(rejection_percent(_get(gd, car_id), "rejected"), 2)},
                    "ort": {"percent": php_round(rejection_percent(_get(pt, car_id), "rejected"), 2)},
                    "cs": {"percent": php_round(rejection_percent(_get(cs, car_id), "totalSortout"), 2)},
                },
            }
        )
    return rows


def _get(summary: Any, car_id: int) -> Any:
    """GD/PT summaries are ``False`` when empty; CS is a dict keyed by carousel."""
    return summary.get(car_id) if isinstance(summary, Mapping) else None
