"""FastAPI application factory (agent.md §7.2, §7.6, §7.9).

Run with::

    uvicorn app.main:app --loop app.core.db:selector_loop_factory
"""

from __future__ import annotations

import json
import logging
import logging.config
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import legacy as legacy_api
from app.api import v1 as v1_api
from app.core.config import Settings, get_settings
from app.core.db import (
    PlantDatabases,
    configure_event_loop_policy,
    create_central_engine,
    create_central_sessionmaker,
)

logger = logging.getLogger("prodash.main")

REQUEST_ID_HEADER = "X-Request-ID"


class _JsonFormatter(logging.Formatter):
    """One JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _configure_logging(settings: Settings) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {"()": _JsonFormatter},
                "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json" if settings.log_json else "plain",
                }
            },
            "loggers": {"prodash": {"level": settings.log_level, "handlers": ["console"]}},
        }
    )


def _error_response(
    request: Request,
    http_status: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    """Build the v1 error envelope (§7.6). Never carries stack traces, SQL or DSNs."""
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    body = {
        "status": "failure",
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": request_id,
    }
    return JSONResponse(body, status_code=http_status, headers={REQUEST_ID_HEADER: request_id})


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(
        exc.status_code, f"HTTP_{exc.status_code}"
    )
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    response = _error_response(request, exc.status_code, code, message)
    for name, value in (exc.headers or {}).items():
        response.headers[name] = value
    return response


async def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = jsonable_encoder(exc.errors(), exclude={"input", "ctx", "url"})
    return _error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "VALIDATION_ERROR",
        "Request validation failed",
        {"errors": errors},
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled exception method=%s path=%s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return _error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "INTERNAL_ERROR",
        "An unexpected error occurred",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application: routers, middleware, exception handlers and lifespan."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_event_loop_policy()
        _configure_logging(cfg)
        engine = create_central_engine(cfg)
        app.state.settings = cfg
        app.state.central_engine = engine
        app.state.central_sessionmaker = create_central_sessionmaker(engine)
        app.state.plant_databases = PlantDatabases(cfg)
        logger.info("startup complete env=%s", cfg.app_env)
        try:
            yield
        finally:
            await app.state.plant_databases.dispose()
            await engine.dispose()
            logger.info("shutdown complete")

    docs = cfg.docs_active
    app = FastAPI(
        title=cfg.app_name,
        version="0.1.0",
        debug=cfg.debug,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    app.include_router(v1_api.router, prefix=cfg.api_v1_prefix)
    if cfg.legacy_api_enabled:
        app.include_router(legacy_api.router, prefix=cfg.legacy_prefix)

    return app


app = create_app()
