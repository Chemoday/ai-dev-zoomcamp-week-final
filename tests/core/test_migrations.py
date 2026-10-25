import asyncio

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.migrations import (
    MigrationAction,
    alembic_config,
    plan_migration,
    run_startup_migrations,
)
from src.core.models_registry import Base
from tests.db_utils import scratch_database


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


# --- Schema equality: what alembic check cannot see ---------------------------------------------

_CONSTRAINTS_SQL = """
SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE connamespace = 'public'::regnamespace AND conrelid::regclass::text <> 'alembic_version'
"""
_INDEXES_SQL = """
SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename <> 'alembic_version'
"""


async def _schema_fingerprint(database_url: str) -> dict[str, set[tuple[str, ...]]]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        constraints = {tuple(row) for row in await connection.execute(text(_CONSTRAINTS_SQL))}
        indexes = {tuple(row) for row in await connection.execute(text(_INDEXES_SQL))}
    await engine.dispose()
    return {"constraints": constraints, "indexes": indexes}


async def test_migrations_build_exactly_the_schema_the_models_describe(database_url: str) -> None:
    # alembic check ignores CHECK constraints and triggers; compare the real catalogs instead,
    # after a full upgrade -> downgrade -> upgrade round trip of every migration.
    async with (
        scratch_database(database_url) as from_models,
        scratch_database(database_url) as from_migrations,
    ):
        engine = create_async_engine(from_models)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

        config = alembic_config(from_migrations)
        await asyncio.to_thread(command.upgrade, config, "head")
        await asyncio.to_thread(command.downgrade, config, "base")
        await asyncio.to_thread(command.upgrade, config, "head")

        expected = await _schema_fingerprint(from_models)
        actual = await _schema_fingerprint(from_migrations)

    assert actual["constraints"] == expected["constraints"]
    assert actual["indexes"] == expected["indexes"]
    assert any(
        name == "ck_meters_readings_delta_consistent" for _, name, _ in actual["constraints"]
    )


async def test_migrations_install_the_audit_log_triggers(db_engine) -> None:
    async with db_engine.connect() as connection:
        triggers = set(
            await connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'meters_reading_logs'::regclass AND NOT tgisinternal"
                )
            )
        )

    assert triggers == {"meters_reading_logs_append_only", "meters_reading_logs_no_truncate"}
