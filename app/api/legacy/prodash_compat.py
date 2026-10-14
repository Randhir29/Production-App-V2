"""PHP-compatible paths and shapes for E01-E06 (agent.md §5.8, §6)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from app.api.deps import PLANT_PATTERN, ConnectivityServiceDep, MetaServiceDep, PlantServiceDep

router = APIRouter(tags=["legacy"])


@router.get("/getPlants", summary="Legacy getPlants (E01)")
async def get_plants(service: PlantServiceDep) -> dict[str, Any]:
    """Static plants as ``{"plants": [{value, text}]}``."""
    return {"plants": service.list_static()}


@router.get("/getPlants2", summary="Legacy getPlants2 (E02)")
async def get_plants2(service: PlantServiceDep) -> dict[str, Any]:
    """Registry plants as ``{"plants": [{value, text, zone, carousals_count}]}``."""
    return {"plants": await service.list_registry()}


@router.get("/zone-maps", summary="Legacy zone-maps (E03)")
async def zone_maps(service: PlantServiceDep) -> list[dict[str, Any]]:
    """``[{zone, plant}]`` for the mailer job."""
    return await service.zone_map()


@router.get("/getPlantName", summary="Legacy getPlantName (E04)")
async def get_plant_name(service: MetaServiceDep) -> dict[str, str]:
    """``{"name": plant_name}``."""
    return {"name": service.plant_name()}


@router.get("/getDeviceName", summary="Legacy getDeviceName (E05)")
async def get_device_name(request: Request, service: MetaServiceDep) -> dict[str, str | None]:
    """``{"client": reverse_dns | null}``."""
    peer = request.client.host if request.client else None
    return {"client": await service.client_device(peer, request.headers.get("x-forwarded-for"))}


@router.get("/getConnectivityStatus", summary="Legacy getConnectivityStatus without plant (E06)")
async def get_connectivity_status_no_plant(service: ConnectivityServiceDep) -> dict[str, Any]:
    """No plant given: ``Unknown Plant``."""
    return await service.check(None)


@router.get("/getConnectivityStatus/{plant}", summary="Legacy getConnectivityStatus (E06)")
async def get_connectivity_status(
    plant: Annotated[str, Path(pattern=PLANT_PATTERN)], service: ConnectivityServiceDep
) -> dict[str, Any]:
    """``{status, message, latency?}``."""
    return await service.check(plant)


# ---- E19-E24: scales and tables (agent.md §5.5, §5.6) ---------------------------------


from fastapi import Form

from app.api.deps import ScaleServiceDep, TableServiceDep
from app.core.errors import AppError

DATABASE_EXCEPTION = r"CodeIgniter\Database\Exceptions\DatabaseException"
CONNECT_FAILURE = "Unable to connect to selected Plant!"
FETCH_OK = "Data Fetched Successfully!"

PlantForm = Annotated[str, Form(pattern=PLANT_PATTERN)]


def _table_failure() -> dict[str, Any]:
    """Legacy failure payload; ``th`` was an exception dump and is returned empty (§7.6)."""
    return {"status": "failure", "error": CONNECT_FAILURE, "th": {}}


@router.get(
    "/getUnderPerformingScales/{plant}/{rowCount}/{period}/{from_ts}/{to_ts}",
    summary="Legacy getUnderPerformingScales (E19)",
)
async def get_under_performing(
    plant: Annotated[str, Path(pattern=PLANT_PATTERN)],
    rowCount: int,
    period: int,
    from_ts: int,
    to_ts: int,
    service: ScaleServiceDep,
) -> dict[str, Any]:
    """``{rows, meta}``; plant errors give the DatabaseException marker the frontend checks."""
    try:
        return await service.under_performing(plant, rowCount or 10, period, from_ts, to_ts)
    except AppError:
        return {"exception_type": DATABASE_EXCEPTION}


@router.get(
    "/getInaccurateScales/{plant}/{rowCount}/{period}/{from_ts}/{to_ts}",
    summary="Legacy getInaccurateScales (E20)",
)
async def get_inaccurate(
    plant: Annotated[str, Path(pattern=PLANT_PATTERN)],
    rowCount: int,
    period: int,
    from_ts: int,
    to_ts: int,
    service: ScaleServiceDep,
) -> dict[str, Any]:
    """``{rows, meta}``."""
    try:
        return await service.inaccurate(plant, rowCount or 5, period, from_ts, to_ts)
    except AppError:
        return {"exception_type": DATABASE_EXCEPTION}


@router.post("/getScaleEfficiencyDetailsTableData2", summary="Legacy scale efficiency (E21)")
async def post_scale_efficiency(
    service: ScaleServiceDep,
    plant: PlantForm,
    from_ms: Annotated[int, Form(alias="from")],
    to_ms: Annotated[int, Form(alias="to")],
    carousal: Annotated[int, Form()] = 0,
) -> dict[str, Any]:
    """``{status, message, tabledata, tableparams}``."""
    try:
        data = await service.efficiency_table(plant, from_ms, to_ms, carousal)
    except AppError:
        return _table_failure()
    return {
        "status": "success",
        "message": FETCH_OK,
        "tabledata": data["rows"],
        "tableparams": {"carousals": data["carousals"]},
    }


@router.post("/getFillingStatistics", summary="Legacy filling statistics (E22)")
async def post_filling_statistics(
    service: TableServiceDep,
    plant: PlantForm,
    from_ms: Annotated[int, Form(alias="from")],
    to_ms: Annotated[int, Form(alias="to")],
    carousal: Annotated[int, Form(ge=1, le=2)],
    cylType: Annotated[int, Form(ge=1, le=2)],
) -> dict[str, Any]:
    """``{status, message, tabledata}`` or ``{status: failure, errors: null}`` when empty (D-18)."""
    try:
        rows = await service.filling_statistics(plant, from_ms, to_ms, carousal, cylType)
    except AppError:
        return _table_failure()
    if not rows:
        return {"status": "failure", "errors": None}
    return {"status": "success", "message": FETCH_OK, "tabledata": rows}


@router.post("/getAccuracyTrend", summary="Legacy accuracy trend (E23)")
async def post_accuracy_trend(
    service: TableServiceDep,
    plant: PlantForm,
    from_dt: Annotated[str, Form(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")],
    to_dt: Annotated[str, Form(alias="to", pattern=r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")],
    carousal: Annotated[int, Form(ge=1, le=2)],
    cylType: Annotated[int, Form(ge=1, le=2)],
) -> dict[str, Any]:
    """``{status: success, data: rows | null}``."""
    try:
        rows = await service.accuracy_trend(plant, from_dt, to_dt, carousal, cylType)
    except AppError:
        return _table_failure()
    return {"status": "success", "data": rows or None}


@router.post("/getHourlyProductionTableData", summary="Legacy periodic production (E24)")
async def post_hourly_production(
    service: TableServiceDep,
    plant: PlantForm,
    from_ms: Annotated[int, Form(alias="from")],
    to_ms: Annotated[int, Form(alias="to")],
    interval: Annotated[int, Form(ge=1, le=1440)] = 60,
) -> dict[str, Any]:
    """``{status, message, tabledata}``; ``No data found!`` when empty."""
    try:
        rows = await service.periodic_production(plant, from_ms, to_ms, interval)
    except AppError:
        return _table_failure()
    if not rows:
        return {"status": "success", "message": "No data found!", "tabledata": []}
    return {"status": "success", "message": FETCH_OK, "tabledata": rows}
