"""FastAPI application factory: lifespan (services), middleware (request id, metering, metrics), errors, routers."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from redis.asyncio import Redis

from .. import __version__
from ..channels.telegram.router import router as telegram_router
from ..config import get_settings
from ..db import Database
from ..domain.metering import Meter
from ..errors import AppError
from ..logging import configure_logging, get_logger
from ..money import UnitRegistry
from .routers import (
    accounts,
    admin,
    annotations,
    balances,
    health,
    interactions,
    keys,
    me,
    metrics,
    reports,
    tags,
    transactions,
    units,
)
from .state import AppState

log = get_logger("finassis.api")

REQ_COUNT = Counter("finassis_http_requests_total", "HTTP requests", ["method", "route", "status"])
REQ_LATENCY = Histogram("finassis_http_request_seconds", "HTTP request latency", ["method", "route"])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, json=(s.env != "dev"))
    db = Database(s)
    await db.connect()
    redis: Redis | None = None
    try:
        redis = Redis.from_url(s.redis_url, decode_responses=True)
        await redis.ping()
    except Exception as e:
        log.warning("redis.unavailable", error=str(e))
        redis = None
    st = AppState(db=db, redis=redis, meter=Meter(db, redis), units=UnitRegistry())
    await st.refresh_units()
    app.state.services = st
    log.info("api.started", version=__version__, env=s.env, telegram=bool(s.telegram_bot_token))
    try:
        yield
    finally:
        if redis is not None:
            await redis.aclose()
        await db.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Finassis API", version=__version__, lifespan=lifespan,
        openapi_url=f"{s.api_prefix}/openapi.json", docs_url=f"{s.api_prefix}/docs", redoc_url=None,
    )

    @app.middleware("http")
    async def _observe(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQ_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQ_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
        response.headers["x-request-id"] = rid
        p = getattr(request.state, "principal", None)
        if p is not None and p.effective_user_id is not None and path.startswith(s.api_prefix) and not path.endswith("/health"):
            await app.state.services.meter.record(p.effective_user_id, "api.request", 1, ref_id=rid)
        return response

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": "validation", "message": "invalid request", "details": {"errors": exc.errors()}})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled", error=str(exc))
        return JSONResponse(status_code=500, content={"code": "internal", "message": "internal error", "details": {}})

    v1 = s.api_prefix
    app.include_router(health.router, prefix=v1)
    app.include_router(me.router, prefix=v1)
    app.include_router(units.router, prefix=v1)
    app.include_router(tags.router, prefix=v1)
    app.include_router(accounts.router, prefix=v1)
    app.include_router(transactions.router, prefix=v1)
    app.include_router(balances.router, prefix=v1)
    app.include_router(reports.router, prefix=v1)
    app.include_router(annotations.router, prefix=v1)
    app.include_router(interactions.router, prefix=v1)
    app.include_router(keys.router, prefix=v1)
    app.include_router(admin.router, prefix=v1)
    app.include_router(metrics.router)
    app.include_router(telegram_router)  # answers 404 until FINASSIS_TELEGRAM_BOT_TOKEN is set
    return app
