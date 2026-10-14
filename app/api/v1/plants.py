"""Plant listings and connectivity, E01-E03 and E06 (agent.md §5.8, §6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import PLANT_PATTERN, ConnectivityServiceDep, PlantServiceDep
from app.schemas.plants import (
    ConnectivityResponse,
    PlantListResponse,
    RegistryResponse,
    ZoneMapEntry,
)

router = APIRouter(prefix="/plants", tags=["plants"])


@router.get(
    "",
    response_model=PlantListResponse,
    summary="List static plants",
    description="Static plant list sorted by short_name (agent.md §5.8, E01).",
)
async def list_plants(service: PlantServiceDep) -> PlantListResponse:
    """Return plants from the static configuration."""
    return PlantListResponse.model_validate({"plants": service.list_static()})


@router.get(
    "/registry",
    response_model=RegistryResponse,
    summary="List registered plants",
    description="Plants from the central database with zone and carousel count (§5.8, E02).",
)
async def list_registry(service: PlantServiceDep) -> RegistryResponse:
    """Return plants from the central registry."""
    return RegistryResponse.model_validate({"plants": await service.list_registry()})


@router.get(
    "/zone-map",
    response_model=list[ZoneMapEntry],
    summary="Plant to zone map",
    description="One entry per registered plant (§5.8, E03).",
)
async def zone_map(service: PlantServiceDep) -> list[ZoneMapEntry]:
    """Return the zone of every plant."""
    return [ZoneMapEntry.model_validate(e) for e in await service.zone_map()]


@router.get(
    "/{plant}/connectivity",
    response_model=ConnectivityResponse,
    response_model_exclude_none=True,
    summary="Plant database connectivity",
    description=(
        "Probes the plant database and reports latency in ms (§5.8, E06). Failures are "
        "reported in the body with HTTP 200, as in the legacy API; internals are never echoed."
    ),
)
async def connectivity(
    plant: Annotated[str, Path(pattern=PLANT_PATTERN)], service: ConnectivityServiceDep
) -> ConnectivityResponse:
    """Check one plant."""
    return ConnectivityResponse.model_validate(await service.check(plant))
