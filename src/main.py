"""FastAPI application factory and ASGI entry point (``uvicorn src.main:app``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.config import Settings, get_settings
from src.core.database import engine
from src.core.demo import router as demo_router
from src.core.exceptions import register_exception_handlers
from src.core.health import router as health_router
from src.core.telemetry import configure_logging, setup_telemetry, shutdown_tracing
from src.modules.communication.router import router as communication_router
from src.modules.meters.router import router as meters_router
from src.modules.property.router import router as property_router
from src.plugins.registry import build_plugin_registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    shutdown_tracing()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)

    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(property_router)
    app.include_router(meters_router)
    app.include_router(communication_router)
    app.include_router(demo_router)
    # One registry per app instance, so enabling a plugin never leaks between apps or tests.
    app.state.plugin_registry = build_plugin_registry(settings.ENABLED_PLUGINS)

    setup_telemetry(app, settings)
    return app


app = create_app()
