"""Database helpers shared by tests that need more than the rolled-back test transaction."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine


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
