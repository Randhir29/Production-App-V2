"""Table routes, E21-E24 (agent.md §5.5.4-§5.6, §6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import PLANT_PATTERN, ScaleServiceDep, TableServiceDep
from app.schemas.scales import (
    AccuracyTrendQuery,
    AccuracyTrendRow,
    FillingStatisticsQuery,
    FillingStatisticsRow,
    PeriodicProductionQuery,
    PeriodicProductionRow,
    ScaleEfficiencyQuery,
    ScaleEfficiencyResponse,
)

router = APIRouter(prefix="/plants/{plant}/tables", tags=["tables"])

PlantPath = Annotated[str, Path(pattern=PLANT_PATTERN, description="Plant short_name.")]


@router.get(
    "/scale-efficiency",
    response_model=ScaleEfficiencyResponse,
    response_model_exclude_none=True,
    summary="Scale efficiency table",
    description="Efficiency per scale and carousel over a window (agent.md §5.5.4, E21).",
)
async def scale_efficiency(
    plant: PlantPath, query: Annotated[ScaleEfficiencyQuery, Query()], service: ScaleServiceDep
) -> ScaleEfficiencyResponse:
    """Return per-scale efficiency pivoted by scale."""
    data = await service.efficiency_table(plant, query.from_ms, query.to_ms, query.carousel)
    return ScaleEfficiencyResponse.model_validate(
        {"rows": data["rows"], "meta": data["meta"], "carousel": data["carousals"]}
    )


@router.get(
    "/filling-statistics",
    response_model=list[FillingStatisticsRow],
    summary="Filling statistics",
    description=(
        "Per-scale variation histogram, mean and std-dev of accepted fills (agent.md §5.5.5, "
        "E22). An empty list means no data (the legacy failure payload, D-18, is legacy only)."
    ),
)
async def filling_statistics(
    plant: PlantPath, query: Annotated[FillingStatisticsQuery, Query()], service: TableServiceDep
) -> list[FillingStatisticsRow]:
    """Return filling statistics per scale."""
    rows = await service.filling_statistics(plant, query.from_ms, query.to_ms, query.carousel, query.cyl_type)
    return [FillingStatisticsRow.model_validate(r) for r in rows]


@router.get(
    "/accuracy-trend",
    response_model=list[AccuracyTrendRow],
    summary="Accuracy trend",
    description="15-minute share of zero-variation fills within ±300 g (agent.md §5.5.6, E23).",
)
async def accuracy_trend(
    plant: PlantPath, query: Annotated[AccuracyTrendQuery, Query()], service: TableServiceDep
) -> list[AccuracyTrendRow]:
    """Return the accuracy trend buckets."""
    rows = await service.accuracy_trend(plant, query.from_, query.to, query.carousel, query.cyl_type)
    return [AccuracyTrendRow.model_validate(r) for r in rows]


@router.get(
    "/periodic-production",
    response_model=list[PeriodicProductionRow],
    response_model_by_alias=True,
    summary="Periodic production",
    description=(
        "Production per period and carousel/cylinder type (agent.md §5.6, E24). Bucketing uses "
        "integer division of the minute, so intervals over 60 start on the hour."
    ),
)
async def periodic_production(
    plant: PlantPath, query: Annotated[PeriodicProductionQuery, Query()], service: TableServiceDep
) -> list[PeriodicProductionRow]:
    """Return production per period."""
    rows = await service.periodic_production(plant, query.from_ms, query.to_ms, query.interval_min)
    return [PeriodicProductionRow.model_validate(r) for r in rows]
