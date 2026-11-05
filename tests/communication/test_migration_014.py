"""Migration 014 (E8/P4): a downgrade drops the board topics with their comments."""

import asyncio
import uuid
from datetime import UTC, datetime

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.migrations import alembic_config
from tests.db_utils import scratch_database


async def test_the_downgrade_removes_board_topics_with_their_comments(database_url: str) -> None:
    async with scratch_database(database_url) as url:
        config = alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "014")
        engine = create_async_engine(url)
        topic, board_topic = uuid.uuid4(), uuid.uuid4()
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
            for post_id, kind, deleted in (
                (topic, "FORUM_TOPIC", False),
                (board_topic, "BOARD_TOPIC", True),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO comm_posts (id, building_id, author_user_id, post_type, "
                        "title, body, deleted_at, deleted_by_user_id) VALUES (:id, :building, "
                        ":author, :kind, 'T', 'B', :deleted_at, :deleted_by)"
                    ),
                    {
                        "id": post_id,
                        "building": building,
                        "author": author,
                        "kind": kind,
                        "deleted_at": datetime(2026, 11, 5, 10, tzinfo=UTC) if deleted else None,
                        "deleted_by": author if deleted else None,
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

        await asyncio.to_thread(command.downgrade, config, "013")

        engine = create_async_engine(url)
        async with engine.connect() as connection:
            posts = set(await connection.scalars(text("SELECT id FROM comm_posts")))
            commented = set(await connection.scalars(text("SELECT post_id FROM comm_comments")))
        await engine.dispose()

    assert posts == commented == {topic}
