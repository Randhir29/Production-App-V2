"""Meta endpoints, E04-E05 (agent.md §5.8, §6)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import MetaServiceDep
from app.schemas.plants import ClientDeviceResponse, PlantNameResponse

router = APIRouter(prefix="/meta", tags=["plants"])


@router.get(
    "/plant-name",
    response_model=PlantNameResponse,
    summary="Configured plant name",
    description="The static plant_name (§5.8, E04).",
)
async def plant_name(service: MetaServiceDep) -> PlantNameResponse:
    """Return the configured plant name."""
    return PlantNameResponse(name=service.plant_name())


@router.get(
    "/client-device",
    response_model=ClientDeviceResponse,
    summary="Caller device name",
    description=(
        "Reverse-DNS name of the caller with a 1 s timeout; X-Forwarded-For is honoured only "
        "from TRUSTED_PROXIES (§5.8, E05)."
    ),
)
async def client_device(request: Request, service: MetaServiceDep) -> ClientDeviceResponse:
    """Return the caller's device name or null."""
    peer = request.client.host if request.client else None
    client = await service.client_device(peer, request.headers.get("x-forwarded-for"))
    return ClientDeviceResponse(client=client)
