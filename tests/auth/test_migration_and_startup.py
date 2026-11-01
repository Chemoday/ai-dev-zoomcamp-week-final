"""Migration 008 (canonical e-mails) and the startup checks of module auth."""

import asyncio
import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import Settings, get_settings
from src.core.migrations import alembic_config
from src.main import create_app
from tests.db_utils import scratch_database


async def _insert_users(url: str, emails: list[str]) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        building = uuid.uuid4()
        await connection.execute(
            text("INSERT INTO property_buildings (id, name, address) VALUES (:id, 'B', 'A')"),
            {"id": building},
        )
        for email in emails:
            await connection.execute(
                text(
                    "INSERT INTO core_users (id, building_id, email, full_name) "
                    "VALUES (:id, :building, :email, 'X')"
                ),
                {"id": uuid.uuid4(), "building": building, "email": email},
            )
    await engine.dispose()


async def _emails(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        rows = set(await connection.scalars(text("SELECT email FROM core_users")))
    await engine.dispose()
    return rows


async def test_the_migration_stores_e_mails_in_canonical_form(database_url: str) -> None:
    async with scratch_database(database_url) as url:
        config = alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "007")
        await _insert_users(url, ["  Ivan.Petrov@Example.COM ", "mari@example.com"])

        await asyncio.to_thread(command.upgrade, config, "008")

        assert await _emails(url) == {"ivan.petrov@example.com", "mari@example.com"}


async def test_the_migration_stops_when_two_e_mails_differ_only_in_case(
    database_url: str,
) -> None:
    async with scratch_database(database_url) as url:
        config = alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "007")
        await _insert_users(url, ["Ivan@example.com", "ivan@example.com"])

        with pytest.raises(RuntimeError, match="differs only in case"):
            await asyncio.to_thread(command.upgrade, config, "008")


@pytest.mark.parametrize("name", ["JWT_SECRET_KEY", "AUTH_HASH_SECRET"])
def test_the_app_does_not_start_without_its_login_secrets(name: str) -> None:
    settings = Settings(**{**get_settings().model_dump(), name: ""})

    with pytest.raises(RuntimeError, match=name):
        create_app(settings)
