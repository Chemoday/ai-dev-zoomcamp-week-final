import asyncio

from alembic import command
from sqlalchemy import text

from src.core.migrations import (
    MigrationAction,
    alembic_config,
    plan_migration,
    run_startup_migrations,
)


def test_fresh_database_is_upgraded() -> None:
    assert plan_migration(current_revisions=set(), known_revisions={"001", "002"}) is (
        MigrationAction.UPGRADE
    )


def test_database_behind_or_at_head_is_upgraded() -> None:
    assert plan_migration({"001"}, {"001", "002"}) is MigrationAction.UPGRADE
    assert plan_migration({"002"}, {"001", "002"}) is MigrationAction.UPGRADE


def test_database_ahead_of_image_is_skipped_so_rollback_can_start() -> None:
    # Rolled back to an image that only knows 001, but the newer release already applied 002.
    assert plan_migration({"002"}, {"001"}) is MigrationAction.SKIP_SCHEMA_AHEAD


# --- Integration: real PostgreSQL ---------------------------------------------------------------


async def test_models_and_migrations_are_in_sync(db_engine, database_url: str) -> None:
    # Fails when a model changes without a migration or is missing from models_registry.py.
    await asyncio.to_thread(command.check, alembic_config(database_url))


async def test_startup_migrations_upgrade_an_up_to_date_database(db_engine, database_url) -> None:
    action = await asyncio.to_thread(run_startup_migrations, database_url)

    assert action is MigrationAction.UPGRADE


async def test_startup_migrations_skip_when_database_is_ahead_of_the_image(
    db_engine, database_url: str
) -> None:
    async with db_engine.begin() as connection:
        original = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        await connection.execute(
            text("UPDATE alembic_version SET version_num = 'from_newer_release'")
        )
    try:
        action = await asyncio.to_thread(run_startup_migrations, database_url)
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                text("UPDATE alembic_version SET version_num = :original"), {"original": original}
            )

    assert action is MigrationAction.SKIP_SCHEMA_AHEAD
