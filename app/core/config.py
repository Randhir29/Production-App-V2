"""Application settings (agent.md §7.5).

Every value comes from the process environment or ``.env``. Legacy variable names that
appeared in the first ``.env.example`` (``CENTRAL_DB_URL``, ``JWT_SECRET``,
``JWT_EXPIRE_MINUTES``) are accepted as aliases of the canonical §7.5 names.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration. Secrets use ``SecretStr`` so they never reach logs or reprs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_by_name=True,
        validate_by_alias=True,
    )

    # --- application -------------------------------------------------------------------
    app_name: str = Field(default="ProDash API", description="Title shown in OpenAPI.")
    app_env: Literal["development", "test", "production"] = Field(default="development")
    debug: bool = Field(default=False)
    docs_enabled: bool = Field(
        default=False,
        description="Expose Swagger/ReDoc in production (always on outside production).",
    )
    api_v1_prefix: str = Field(default="/api/v1")
    legacy_api_enabled: bool = Field(default=True)
    legacy_prefix: str = Field(default="/prodash")
    timezone: str = Field(default="Asia/Kolkata")

    # --- central database --------------------------------------------------------------
    central_database_url: SecretStr = Field(
        validation_alias=AliasChoices("CENTRAL_DATABASE_URL", "CENTRAL_DB_URL"),
        description="postgresql+psycopg://… or mysql+aiomysql://…",
    )
    central_db_pool_size: int = Field(default=5, ge=1)
    central_db_max_overflow: int = Field(default=10, ge=0)
    central_db_connect_timeout_s: int = Field(default=10, ge=1)
    legacy_mysql_url: SecretStr | None = Field(
        default=None, description="Source for the migrate-central-data CLI."
    )

    # --- plant databases (read-only PostgreSQL) ----------------------------------------
    plant_db_port: int = Field(default=5432, ge=1, le=65535)
    plant_db_pool_size: int = Field(default=2, ge=1)
    plant_db_max_overflow: int = Field(default=3, ge=0)
    plant_db_connect_timeout_s: int = Field(default=10, ge=1)
    plant_db_statement_timeout_ms: int = Field(default=120_000, ge=1)
    plant_engine_cache_max: int = Field(default=64, ge=1)
    plant_engine_ttl_s: int = Field(default=3600, ge=1)
    plant_max_concurrency: int = Field(default=4, ge=1)

    # --- static plant configuration ----------------------------------------------------
    static_plant_config_path: str = Field(default="config/static_plant_config.yaml")

    # --- auth --------------------------------------------------------------------------
    jwt_secret_key: SecretStr = Field(
        validation_alias=AliasChoices("JWT_SECRET_KEY", "JWT_SECRET"),
        min_length=32,
        description="HMAC secret, at least 32 characters.",
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field(default="HS256")
    access_token_expire_minutes: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices("ACCESS_TOKEN_EXPIRE_MINUTES", "JWT_EXPIRE_MINUTES"),
    )
    refresh_token_expire_days: int = Field(default=7, ge=1)
    auth_enabled: bool = Field(default=True)
    service_api_keys: list[str] = Field(
        default_factory=list,
        description="SHA-256 hex digests of the keys accepted for E27 (X-API-Key).",
    )
    login_max_attempts: int = Field(default=5, ge=1, description="Failures before lockout.")
    login_lockout_minutes: int = Field(default=15, ge=1)

    # --- response cache ------------------------------------------------------------------
    response_cache_ttl_s: int = Field(
        default=0, ge=0, le=300, description="In-process TTL for dashboard-polled reads; 0 = off."
    )
    response_cache_max_entries: int = Field(default=1024, ge=1)

    # --- HTTP edge ---------------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=list)
    trusted_proxies: list[str] = Field(default_factory=list)

    # --- logging -----------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_json: bool = Field(default=True)
    slow_query_ms: int = Field(default=5000, ge=1)

    @property
    def docs_active(self) -> bool:
        """Whether Swagger/ReDoc/openapi.json are served (agent.md §7.5)."""
        return self.app_env != "production" or self.docs_enabled


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loading and validating them on first use."""
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
