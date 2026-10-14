"""Scale analytics routes, E19-E20 (agent.md §5.5, §6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import PLANT_PATTERN, ScaleServiceDep
from app.schemas.scales import (
    InaccurateQuery,
    InaccurateResponse,
    UnderPerformingQuery,
    UnderPerformingResponse,
)

router = APIRouter(prefix="/plants/{plant}/scales", tags=["scales"])

PlantPath = Annotated[str, Path(pattern=PLANT_PATTERN, description="Plant short_name.")]


@router.get(
    "/under-performing",
    response_model=UnderPerformingResponse,
    response_model_exclude_none=True,
    summary="Under-performing scales",
    description=(
        "Scales with the lowest efficiency against the rated output, ascending (agent.md §5.5.2, "
        "E19). Sorting truncates differences below 0.01 (D-15). Tags use the frontend thresholds "
        "0.75 and 1."
    ),
)
async def under_performing(
    plant: PlantPath, query: Annotated[UnderPerformingQuery, Query()], service: ScaleServiceDep
) -> UnderPerformingResponse:
    """Return the lowest-efficiency scales."""
    data = await service.under_performing(
        plant, query.row_count, query.period_hours, query.from_ts, query.to_ts, with_tags=True
    )
    return UnderPerformingResponse.model_validate(data)


@router.get(
    "/inaccurate",
    response_model=InaccurateResponse,
    summary="Inaccurate scales",
    description=(
        "Scales with the largest share of fills more than 100 g off, ascending by accuracy "
        "(agent.md §5.5.3, E20). The legacy bound for 19 kg cylinders is preserved (D-01)."
    ),
)
async def inaccurate(
    plant: PlantPath, query: Annotated[InaccurateQuery, Query()], service: ScaleServiceDep
) -> InaccurateResponse:
    """Return the least accurate scales."""
    data = await service.inaccurate(plant, query.row_count, query.period_hours, query.from_ts, query.to_ts)
    return InaccurateResponse.model_validate(data)
