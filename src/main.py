"""FastAPI application factory and ASGI entry point (``uvicorn src.main:app``)."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core.config import Settings, get_settings
from src.core.database import async_session_factory, engine
from src.core.demo import router as demo_router
from src.core.domain_events import run_relay_loop
from src.core.event_bus import InMemoryEventBus
from src.core.exceptions import register_exception_handlers
from src.core.frontend import mount_frontend
from src.core.health import router as health_router
from src.core.telemetry import configure_logging, setup_telemetry, shutdown_tracing
from src.modules.communication.router import router as communication_router
from src.modules.meters.router import router as meters_router
from src.modules.notifications.dispatcher import (
    Dispatcher,
    run_dispatcher_loop,
    validate_channel_settings,
)
from src.modules.notifications.router import router as notifications_router
from src.modules.notifications.service import NotificationsSubscriber
from src.modules.property.router import router as property_router
from src.plugins.registry import build_plugin_registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    loops: list[asyncio.Task[None]] = []
    if settings.DOMAIN_EVENTS_RELAY_ENABLED:
        loops.append(
            asyncio.create_task(
                run_relay_loop(
                    async_session_factory,
                    app.state.event_bus,
                    retention_days=settings.DOMAIN_EVENTS_RETENTION_DAYS,
                ),
                name="domain-event-relay",
            )
        )
    dispatcher: Dispatcher | None = getattr(app.state, "notifications_dispatcher", None)
    if dispatcher is not None and settings.NOTIFICATIONS_DISPATCHER_ENABLED:
        loops.append(
            asyncio.create_task(run_dispatcher_loop(dispatcher), name="notifications-dispatcher")
        )
    yield
    for loop in loops:
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop
    shutdown_tracing()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)

    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)
    app.state.settings = settings
    # One event bus per app instance: handlers subscribed here never leak into another app.
    app.state.event_bus = InMemoryEventBus()
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(property_router)
    app.include_router(meters_router)
    app.include_router(communication_router)
    # One registry per app instance, so enabling a plugin never leaks between apps or tests.
    # Plugins with incomplete settings raise here, so the app does not start half-configured.
    plugins = build_plugin_registry(settings)
    app.state.plugin_registry = plugins
    if settings.NOTIFICATIONS_ENABLED:
        validate_channel_settings(settings, plugins)
        app.include_router(notifications_router)
        # The subscriber and the dispatcher read the factory on every use, so tests can swap it
        # after create_app.
        app.state.notifications_session_factory = async_session_factory
        NotificationsSubscriber(
            lambda: app.state.notifications_session_factory,
            settings.DEFAULT_LOCALE,
            demo_mode=settings.DEMO_MODE_ENABLED,
            plugins=plugins,
        ).subscribe(app.state.event_bus)
        app.state.notifications_dispatcher = Dispatcher(
            lambda: app.state.notifications_session_factory, plugins, settings
        )
    app.include_router(demo_router)
    # After every API route: the client only gets paths that nothing else answered.
    mount_frontend(app, settings.FRONTEND_DIST_DIR)

    setup_telemetry(app, settings)
    return app


app = create_app()
