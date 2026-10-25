"""Database helpers shared by tests that need more than the rolled-back test transaction."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


@asynccontextmanager
async def scratch_database(database_url: str) -> AsyncIterator[str]:
    """A throwaway database on the same server, dropped afterwards."""
    url = make_url(database_url)
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


@asynccontextmanager
async def failing_commit(session: AsyncSession, model: type) -> AsyncIterator[None]:
    """Make the session's next COMMIT fail if it would commit a new row of `model`.

    Simulates a commit that the database rejects (e.g. a lost connection) after all the
    request's work succeeded.
    """
    inserted = False

    def track(sync_session, flush_context, instances) -> None:
        nonlocal inserted
        inserted = inserted or any(isinstance(obj, model) for obj in sync_session.new)

    def reject(sync_session) -> None:
        nonlocal inserted
        if inserted:
            inserted = False
            raise ConnectionError("connection lost during COMMIT")

    event.listen(session.sync_session, "before_flush", track)
    event.listen(session.sync_session, "before_commit", reject)
    try:
        yield
    finally:
        event.remove(session.sync_session, "before_flush", track)
        event.remove(session.sync_session, "before_commit", reject)
