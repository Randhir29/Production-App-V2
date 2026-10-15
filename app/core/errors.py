"""Application error hierarchy (agent.md §7.6). Services raise these; HTTP mapping is in main."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class: carries the HTTP status and envelope code for the v1 API."""

    http_status = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred"

    def __init__(self, message: str | None = None, **details: Any) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details


class PlantNotFound(AppError):
    http_status = 404
    code = "PLANT_NOT_FOUND"
    message = "Plant not found"

    def __init__(self, plant: str) -> None:
        super().__init__(plant=plant)


class PlantConfigMissing(AppError):
    http_status = 409
    code = "PLANT_CONFIG_INCOMPLETE"
    message = "Plant carousel configuration is incomplete"

    def __init__(self, plant: str) -> None:
        super().__init__(plant=plant)


class PlantUnreachable(AppError):
    http_status = 502
    code = "PLANT_UNREACHABLE"
    message = "Unable to connect to selected plant"

    def __init__(self, plant: str) -> None:
        super().__init__(plant=plant)


class PlantQueryError(AppError):
    http_status = 502
    code = "PLANT_QUERY_FAILED"
    message = "Plant query failed"

    def __init__(self, plant: str) -> None:
        super().__init__(plant=plant)


class AuthError(AppError):
    """Missing, invalid or expired credentials (401)."""

    http_status = 401
    code = "UNAUTHENTICATED"
    message = "Authentication required"


class PermissionDenied(AppError):
    """Authenticated but not allowed (403)."""

    http_status = 403
    code = "FORBIDDEN"
    message = "You do not have permission to perform this action"
