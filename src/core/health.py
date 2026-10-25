"""Liveness and readiness probes."""

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.database import DbSession
from src.core.exceptions import DomainError, error_responses

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving requests (no external dependencies checked)."""
    return {"status": "ok"}


@router.get("/health/ready", responses=error_responses({503: ["DATABASE_UNAVAILABLE"]}))
async def readiness(session: DbSession) -> dict[str, str]:
    """Readiness: the database is reachable."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        raise DomainError(
            status_code=503,
            error_code="DATABASE_UNAVAILABLE",
            message="Database is not reachable.",
        ) from exc
    return {"status": "ok", "database": "ok"}
