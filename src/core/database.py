"""Async SQLAlchemy engine, session factory, declarative Base and the request-scoped session."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings


def build_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: DB error texts would otherwise embed bound values (names, emails, phones)
    # and reach logs and spans through the exception message.
    return create_async_engine(
        database_url, echo=False, future=True, pool_pre_ping=True, hide_parameters=True
    )


engine = build_engine(settings.DATABASE_URL)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # CRITICAL: Never set to True!
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one session per request: commit on success, roll back on any exception."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Always depend on the session through this alias. scope="function" ends the dependency (commit or
# rollback) BEFORE the response is sent. FastAPI's default "request" scope would commit after the
# client already got a 2xx, so a failed commit would still look successful.
DbSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]
