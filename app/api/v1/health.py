"""Liveness and readiness routes (agent.md §7.9, E29)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app.services.health import central_db_ready

router = APIRouter(prefix="/health", tags=["health"])


class HealthStatus(BaseModel):
    """Health probe result."""

    status: Literal["ok", "unavailable"] = Field(description="Probe outcome.")


@router.get(
    "/live",
    response_model=HealthStatus,
    summary="Liveness probe",
    description="Always returns 200 while the process is serving requests (§7.9).",
)
async def live() -> HealthStatus:
    """Return ``ok`` while the process is up."""
    return HealthStatus(status="ok")


@router.get(
    "/ready",
    response_model=HealthStatus,
    summary="Readiness probe",
    description="Pings the central database; returns 503 when it is unreachable (§7.9).",
    responses={503: {"model": HealthStatus, "description": "Central database unreachable."}},
)
async def ready(request: Request, response: Response) -> HealthStatus:
    """Return ``ok`` when the central DB answers, otherwise 503."""
    if await central_db_ready(request.app.state.central_engine):
        return HealthStatus(status="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthStatus(status="unavailable")
