"""FastAPI dependencies: settings, central session, plant access, static config (agent.md §7.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.db import PlantDatabases
from app.core.errors import AuthError
from app.domain.static_config import StaticPlantConfig, load_static_config
from app.repositories.central.carousel_repo import CarouselRepository
from app.repositories.central.plant_repo import PlantRepository
from app.repositories.central.user_repo import RefreshTokenRepository, UserRepository
from app.services.auth import (
    READ_ROLES,
    AuthService,
    Principal,
    api_key_matches,
    principal_from_access_token,
    require_any_role,
)
from app.services.carousel_config import CarouselConfigService
from app.services.connectivity import ConnectivityService
from app.services.mailer import MailerService
from app.services.meta import MetaService
from app.services.plant_access import PlantAccess
from app.services.plants import PlantService
from app.services.reports import ReportService
from app.services.scales import ScaleService
from app.services.tables import TableService


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def get_central_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One central-DB session per request."""
    async with request.app.state.central_sessionmaker() as session:
        yield session


CentralSession = Annotated[AsyncSession, Depends(get_central_session)]


def get_static_config(settings: SettingsDep) -> StaticPlantConfig:
    return load_static_config(settings.static_plant_config_path)


StaticConfig = Annotated[StaticPlantConfig, Depends(get_static_config)]


def get_plant_databases(request: Request) -> PlantDatabases:
    databases: PlantDatabases = request.app.state.plant_databases
    return databases


def get_plant_access(
    session: CentralSession,
    databases: Annotated[PlantDatabases, Depends(get_plant_databases)],
    settings: SettingsDep,
) -> PlantAccess:
    return PlantAccess(PlantRepository(session), databases, settings)


def get_plant_service(session: CentralSession, static: StaticConfig) -> PlantService:
    return PlantService(PlantRepository(session), static)


def get_connectivity_service(
    access: Annotated[PlantAccess, Depends(get_plant_access)], static: StaticConfig
) -> ConnectivityService:
    return ConnectivityService(access, static)


def get_meta_service(static: StaticConfig, settings: SettingsDep) -> MetaService:
    return MetaService(static, settings.trusted_proxies)


def get_scale_service(
    request: Request,
    access: Annotated[PlantAccess, Depends(get_plant_access)],
    static: StaticConfig,
) -> ScaleService:
    cache: TTLCache | None = getattr(request.app.state, "response_cache", None)
    return ScaleService(access, static, cache)


def get_table_service(access: Annotated[PlantAccess, Depends(get_plant_access)]) -> TableService:
    return TableService(access)


def get_report_service(
    session: CentralSession, access: Annotated[PlantAccess, Depends(get_plant_access)]
) -> ReportService:
    carousels = CarouselConfigService(PlantRepository(session), CarouselRepository(session))
    return ReportService(access, carousels)


def get_mailer_service(
    session: CentralSession, reports: Annotated[ReportService, Depends(get_report_service)]
) -> MailerService:
    return MailerService(PlantRepository(session), reports)


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
MailerServiceDep = Annotated[MailerService, Depends(get_mailer_service)]
ScaleServiceDep = Annotated[ScaleService, Depends(get_scale_service)]
TableServiceDep = Annotated[TableService, Depends(get_table_service)]
PlantServiceDep = Annotated[PlantService, Depends(get_plant_service)]
ConnectivityServiceDep = Annotated[ConnectivityService, Depends(get_connectivity_service)]
MetaServiceDep = Annotated[MetaService, Depends(get_meta_service)]

PLANT_PATTERN = r"^[a-z0-9]+$"


# ---- authentication and authorisation (agent.md §7.8) ---------------------------------


bearer_scheme = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")
API_KEY_HEADER = "x-api-key"
ADMIN_ROLES = ("admin",)


def get_auth_service(session: CentralSession, settings: SettingsDep) -> AuthService:
    return AuthService(UserRepository(session), RefreshTokenRepository(session), settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _principal(credentials: HTTPAuthorizationCredentials | None, settings: Settings) -> Principal:
    if credentials is None or not credentials.credentials:
        raise AuthError()
    return principal_from_access_token(credentials.credentials, settings)


async def require_user(credentials: Credentials, settings: SettingsDep) -> Principal:
    """Any valid access token (always enforced)."""
    return _principal(credentials, settings)


async def read_access(credentials: Credentials, settings: SettingsDep) -> Principal | None:
    """Guard for read endpoints: viewer, officer or admin. Open when ``AUTH_ENABLED`` is false."""
    if not settings.auth_enabled:
        return None
    principal = _principal(credentials, settings)
    require_any_role(principal, tuple(READ_ROLES))
    return principal


def require_roles(*roles: str):  # type: ignore[no-untyped-def]
    """Guard factory for admin-type endpoints. Always enforced, even with ``AUTH_ENABLED=false``."""

    async def dependency(credentials: Credentials, settings: SettingsDep) -> Principal:
        principal = _principal(credentials, settings)
        require_any_role(principal, roles)
        return principal

    return dependency


async def mailer_access(
    request: Request, credentials: Credentials, settings: SettingsDep
) -> Principal | None:
    """E27: a service API key (``X-API-Key``) or an admin JWT. Open when auth is disabled."""
    if not settings.auth_enabled:
        return None
    presented = request.headers.get(API_KEY_HEADER)
    if presented and api_key_matches(presented, settings.service_api_keys):
        return None
    principal = _principal(credentials, settings)
    require_any_role(principal, ADMIN_ROLES)
    return principal


ReadAccess = Depends(read_access)
MailerAccess = Depends(mailer_access)
