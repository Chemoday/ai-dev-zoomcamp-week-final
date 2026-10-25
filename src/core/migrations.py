"""Startup migration step: ``python -m src.core.migrations`` (run by entrypoint.sh).

Upgrades the database to the newest revision this image knows. During a rollback the database
can already be at a revision from a newer release that this image does not contain; plain
``alembic upgrade head`` would crash the container in that case. Because migrations are kept
backward compatible (expand/contract), the older image can run on the newer schema, so the
step logs a warning and skips instead.
"""

import asyncio
from enum import StrEnum
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import settings
from src.core.telemetry import configure_logging

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

logger = structlog.get_logger(__name__)


class MigrationAction(StrEnum):
    UPGRADE = "upgrade"
    SKIP_SCHEMA_AHEAD = "skip_schema_ahead"


def plan_migration(current_revisions: set[str], known_revisions: set[str]) -> MigrationAction:
    """Decide what to do given the database's current heads and the revisions in this image."""
    if current_revisions - known_revisions:
        return MigrationAction.SKIP_SCHEMA_AHEAD
    return MigrationAction.UPGRADE


def _read_heads(sync_connection) -> tuple[str, ...]:
    return MigrationContext.configure(sync_connection).get_current_heads()


async def _current_revisions(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            heads = await connection.run_sync(_read_heads)
    finally:
        await engine.dispose()
    return set(heads)


def run_startup_migrations() -> MigrationAction:
    config = Config(str(ALEMBIC_INI))
    known = {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}
    current = asyncio.run(_current_revisions(settings.DATABASE_URL))

    action = plan_migration(current, known)
    if action is MigrationAction.SKIP_SCHEMA_AHEAD:
        logger.warning(
            "migrations_skipped_schema_ahead",
            database_revisions=sorted(current),
            image_revisions=sorted(known),
        )
        return action

    logger.info("migrations_upgrading", database_revisions=sorted(current))
    command.upgrade(config, "head")
    return action


if __name__ == "__main__":
    configure_logging(level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)
    run_startup_migrations()
