"""Performance report routes, E25-E27 (agent.md §5.7, §6)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from app.api.deps import PLANT_PATTERN, MailerAccess, MailerServiceDep, ReadAccess, ReportServiceDep
from app.schemas.reports import (
    MailRowsRequest,
    PerformanceV1Query,
    PerformanceV2Query,
    ReportResponse,
)

router = APIRouter(tags=["reports"])

PlantPath = Annotated[str, Path(pattern=PLANT_PATTERN, description="Plant short_name.")]


@router.get(
    "/plants/{plant}/reports/performance",
    dependencies=[ReadAccess],
    response_model=ReportResponse,
    summary="Performance report V1",
    description=(
        "Bottling, productive hours, CS/GD/PT rejections and productivity over a time range "
        "(agent.md §5.7.7, E25). Plant errors use the standard error envelope."
    ),
)
async def performance_v1(
    plant: PlantPath, query: Annotated[PerformanceV1Query, Query()], service: ReportServiceDep
) -> ReportResponse:
    """Return the V1 report."""
    data = await service.performance_v1(plant, query.from_ms, query.to_ms)
    return ReportResponse(data=data)


@router.get(
    "/plants/{plant}/reports/performance-v2",
    dependencies=[ReadAccess],
    response_model=ReportResponse,
    summary="Performance report V2",
    description=(
        "Date-range report split into normal/break/overtime phases with stoppage analysis "
        "(agent.md §5.7.7, E26). Carousels without shift times or breaks give 409 "
        "PLANT_CONFIG_INCOMPLETE (D-06); an unreachable plant gives 502 (D-19)."
    ),
)
async def performance_v2(
    plant: PlantPath, query: Annotated[PerformanceV2Query, Query()], service: ReportServiceDep
) -> ReportResponse:
    """Return the V2 report."""
    data = await service.performance_v2(plant, query.from_date, query.to_date)
    return ReportResponse(data=data)


@router.post(
    "/reports/performance-mail-rows",
    dependencies=[MailerAccess],
    response_model=list[dict[str, Any]],
    summary="Mailer performance rows",
    description=(
        "Flattened per-carousel rows for the external mail job (agent.md §5.7.8, E27). An "
        "unavailable plant yields an empty list."
    ),
)
async def performance_mail_rows(body: MailRowsRequest, service: MailerServiceDep) -> list[dict[str, Any]]:
    """Return one row per carousel of the plant."""
    return await service.performance_rows(body.plant, body.from_, body.to)
