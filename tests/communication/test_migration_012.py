"""Migration 012 (E8/P2): last_activity_at from the newest comment; a downgrade drops threads."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.migrations import alembic_config
from tests.db_utils import scratch_database

CREATED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


async def test_the_migration_fills_the_activity_from_the_newest_comment(database_url: str) -> None:
    quiet, busy = uuid.uuid4(), uuid.uuid4()
    async with scratch_database(database_url) as url:
        config = alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "011")
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            building, author = uuid.uuid4(), uuid.uuid4()
            await connection.execute(
                text("INSERT INTO property_buildings (id, name, address) VALUES (:id, 'B', 'A')"),
                {"id": building},
            )
            await connection.execute(
                text(
                    "INSERT INTO core_users (id, building_id, full_name) "
                    "VALUES (:id, :building, 'X')"
                ),
                {"id": author, "building": building},
            )
            for post_id in (quiet, busy):
                await connection.execute(
                    text(
                        "INSERT INTO comm_posts (id, building_id, author_user_id, post_type, "
                        "title, body, created_at) VALUES "
                        "(:id, :building, :author, 'FORUM_TOPIC', 'T', 'B', :created)"
                    ),
                    {"id": post_id, "building": building, "author": author, "created": CREATED},
                )
            for hours in (2, 5, 3):
                await connection.execute(
                    text(
                        "INSERT INTO comm_comments (id, post_id, building_id, author_user_id, "
                        "body, created_at) VALUES (:id, :post, :building, :author, 'C', :at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "post": busy,
                        "building": building,
                        "author": author,
                        "at": CREATED + timedelta(hours=hours),
                    },
                )
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, config, "012")

        engine = create_async_engine(url)
        async with engine.connect() as connection:
            rows = dict(
                (
                    await connection.execute(text("SELECT id, last_activity_at FROM comm_posts"))
                ).all()
            )
        await engine.dispose()

    assert rows == {quiet: CREATED, busy: CREATED + timedelta(hours=5)}


async def test_the_downgrade_removes_threads_with_their_replies(database_url: str) -> None:
    async with scratch_database(database_url) as url:
        config = alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "012")
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            building, author, apartment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            topic, thread = uuid.uuid4(), uuid.uuid4()
            await connection.execute(
                text("INSERT INTO property_buildings (id, name, address) VALUES (:id, 'B', 'A')"),
                {"id": building},
            )
            await connection.execute(
                text(
                    "INSERT INTO property_apartments (id, building_id, number, area_m2) "
                    "VALUES (:id, :building, '14', 50)"
                ),
                {"id": apartment, "building": building},
            )
            await connection.execute(
                text(
                    "INSERT INTO core_users (id, building_id, full_name) "
                    "VALUES (:id, :building, 'X')"
                ),
                {"id": author, "building": building},
            )
            for post_id, kind, scope, targets in (
                (topic, "FORUM_TOPIC", "ALL_BUILDING", None),
                (thread, "APARTMENT_THREAD", "APARTMENT_LIST", f'["{apartment}"]'),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO comm_posts (id, building_id, author_user_id, post_type, "
                        "title, body, target_scope, target_apartment_ids) VALUES (:id, "
                        ":building, :author, :kind, 'T', 'B', :scope, CAST(:targets AS jsonb))"
                    ),
                    {
                        "id": post_id,
                        "building": building,
                        "author": author,
                        "kind": kind,
                        "scope": scope,
                        "targets": targets,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO comm_comments (id, post_id, building_id, author_user_id, "
                        "body) VALUES (:id, :post, :building, :author, 'C')"
                    ),
                    {"id": uuid.uuid4(), "post": post_id, "building": building, "author": author},
                )
        await engine.dispose()

        await asyncio.to_thread(command.downgrade, config, "011")

        engine = create_async_engine(url)
        async with engine.connect() as connection:
            posts = set(await connection.scalars(text("SELECT id FROM comm_posts")))
            commented = set(await connection.scalars(text("SELECT post_id FROM comm_comments")))
        await engine.dispose()

    assert posts == commented == {topic}
