"""PHP-compatible paths and shapes for E25-E27 (agent.md §5.7, §6)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, Response

from app.api.deps import MailerAccess, MailerServiceDep, ReadAccess, ReportServiceDep
from app.api.legacy.prodash_compat import FETCH_OK, PlantForm, _table_failure
from app.core.errors import AppError, PlantConfigMissing
from app.schemas.reports import MailRowsRequest

router = APIRouter(tags=["legacy"])


def _report_ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "message": FETCH_OK, "data": data}


@router.post("/getPerformanceReport", dependencies=[ReadAccess], summary="Legacy performance report V1 (E25)")
async def post_performance_report(
    service: ReportServiceDep,
    plant: PlantForm,
    from_ms: Annotated[int, Form(alias="from")],
    to_ms: Annotated[int, Form(alias="to")],
) -> dict[str, Any]:
    """V1 report; any plant error gives the legacy failure payload."""
    try:
        return _report_ok(await service.performance_v1(plant, from_ms, to_ms))
    except AppError:
        return _table_failure()


async def _v2(service: ReportServiceDep, plant: str, from_d: date, to_d: date) -> dict[str, Any]:
    try:
        return _report_ok(await service.performance_v2(plant, from_d, to_d))
    except PlantConfigMissing:
        raise  # D-06: 409 PLANT_CONFIG_INCOMPLETE
    except AppError as exc:
        raise AppError() from exc  # D-19: legacy re-threw, giving a generic 500


@router.post(
    "/getPerformanceReportV2", dependencies=[ReadAccess], summary="Legacy performance report V2 (E26)"
)
async def post_performance_report_v2(
    service: ReportServiceDep,
    plant: PlantForm,
    from_d: Annotated[date, Form(alias="from")],
    to_d: Annotated[date, Form(alias="to")],
) -> dict[str, Any]:
    """V2 report."""
    return await _v2(service, plant, from_d, to_d)


@router.post(
    "/getPerformanceReportV2Test2", dependencies=[ReadAccess], summary="Alias of getPerformanceReportV2 (E26)"
)
async def post_performance_report_v2_alias(
    service: ReportServiceDep,
    plant: PlantForm,
    from_d: Annotated[date, Form(alias="from")],
    to_d: Annotated[date, Form(alias="to")],
) -> dict[str, Any]:
    """Alias route kept for the existing frontend."""
    return await _v2(service, plant, from_d, to_d)


@router.post("/performance-report", dependencies=[MailerAccess], summary="Legacy mailer feed (E27)")
async def post_performance_mail_rows(
    body: MailRowsRequest, service: MailerServiceDep, response: Response
) -> list[dict[str, Any]]:
    """Mailer rows; CORS ``*`` as in the legacy route (agent.md §5.7.8)."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    return await service.performance_rows(body.plant, body.from_, body.to)
