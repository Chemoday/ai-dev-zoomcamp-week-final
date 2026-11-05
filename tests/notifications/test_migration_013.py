"""Migration 013 (E8/P3): a downgrade drops the rows of the thread event types, keeps the rest."""

import asyncio
import uuid

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.migrations import alembic_config
from tests.db_utils import scratch_database


async def test_the_downgrade_removes_only_the_thread_event_rows(database_url: str) -> None:
    async with scratch_database(database_url) as url:
        config = alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "013")
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            building, user = uuid.uuid4(), uuid.uuid4()
            await connection.execute(
                text("INSERT INTO property_buildings (id, name, address) VALUES (:id, 'B', 'A')"),
                {"id": building},
            )
            await connection.execute(
                text(
                    "INSERT INTO core_users (id, building_id, full_name) "
                    "VALUES (:id, :building, 'X')"
                ),
                {"id": user, "building": building},
            )
            for event_type in ("PERIOD_REVIEW", "APARTMENT_MESSAGE_TO_BOARD"):
                await connection.execute(
                    text(
                        "INSERT INTO notif_preferences (id, user_id, event_type, channel, "
                        "enabled, updated_at) VALUES (:id, :user, :event_type, 'email', true, "
                        "now())"
                    ),
                    {"id": uuid.uuid4(), "user": user, "event_type": event_type},
                )
                await connection.execute(
                    text(
                        "INSERT INTO notif_deliveries (id, user_id, channel, event_type, "
                        "dedup_key, subject_type, subject_id, locale, priority, status, "
                        "next_attempt_at, created_at) VALUES (:id, :user, 'in_app', :event_type, "
                        ":key, 'post', :subject, 'en', 1, 'SENT', now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "user": user,
                        "event_type": event_type,
                        "key": f"{event_type}:{uuid.uuid4()}",
                        "subject": uuid.uuid4(),
                    },
                )
        await engine.dispose()

        await asyncio.to_thread(command.downgrade, config, "012")

        engine = create_async_engine(url)
        async with engine.connect() as connection:
            kept = {
                table: set(await connection.scalars(text(f"SELECT event_type FROM {table}")))
                for table in ("notif_preferences", "notif_deliveries")
            }
        await engine.dispose()

    assert kept == {"notif_preferences": {"PERIOD_REVIEW"}, "notif_deliveries": {"PERIOD_REVIEW"}}
