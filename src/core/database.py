"""Async SQLAlchemy engine, session factory, declarative Base and the request-scoped session."""

import uuid
from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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


# --- Column helpers shared by all modules -------------------------------------------------------


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def varchar_enum(enum_cls: type[StrEnum], check_name: str, length: int = 32) -> SAEnum:
    """Store a StrEnum as VARCHAR (no native PostgreSQL enum type), as docs/SCHEMA.md specifies,
    with a named CHECK constraint so a stray value cannot be written and later crash every read.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        name=check_name,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


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
