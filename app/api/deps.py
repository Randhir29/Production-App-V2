"""FastAPI dependencies: settings, central session, plant access, static config (agent.md §7.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import PlantDatabases
from app.domain.static_config import StaticPlantConfig, load_static_config
from app.repositories.central.plant_repo import PlantRepository
from app.services.connectivity import ConnectivityService
from app.services.meta import MetaService
from app.services.plant_access import PlantAccess
from app.services.plants import PlantService
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
    access: Annotated[PlantAccess, Depends(get_plant_access)], static: StaticConfig
) -> ScaleService:
    return ScaleService(access, static)


def get_table_service(access: Annotated[PlantAccess, Depends(get_plant_access)]) -> TableService:
    return TableService(access)


ScaleServiceDep = Annotated[ScaleService, Depends(get_scale_service)]
TableServiceDep = Annotated[TableService, Depends(get_table_service)]
PlantServiceDep = Annotated[PlantService, Depends(get_plant_service)]
ConnectivityServiceDep = Annotated[ConnectivityService, Depends(get_connectivity_service)]
MetaServiceDep = Annotated[MetaService, Depends(get_meta_service)]

PLANT_PATTERN = r"^[a-z0-9]+$"
