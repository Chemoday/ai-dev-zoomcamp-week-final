"""The hourly clean-up of the auth module (docs/SCHEMA.md 7, retention).

A2 deletes ended sessions (their refresh tokens cascade); A3-A5 add links, attempts and
invitations. Each tick is one short transaction under pg_try_advisory_xact_lock, so several
processes never delete at the same time.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, or_, text

from src.core.domain_events import SessionFactory
from src.modules.auth.models import AuthSession
from src.modules.auth.policies import SESSION_RETENTION

logger = structlog.get_logger(__name__)
CLEANUP_INTERVAL = timedelta(hours=1)
CLEANUP_LOCK_KEY = "auth:cleanup"
_sleep = asyncio.sleep  # tests replace it, never the global asyncio.sleep


async def cleanup_tick(session_factory: SessionFactory, now: datetime | None = None) -> int | None:
    """Delete ended sessions; returns how many, or None when another process held the lock."""
    now = now or datetime.now(UTC)
    async with session_factory() as session:
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": CLEANUP_LOCK_KEY},
        )
        if not locked:
            return None
        cutoff = now - SESSION_RETENTION
        result = await session.execute(
            delete(AuthSession)
            .where(or_(AuthSession.revoked_at < cutoff, AuthSession.expires_at < cutoff))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("auth_cleanup", deleted_sessions=deleted)
    return deleted


async def run_cleanup_loop(session_factory_provider: Callable[[], SessionFactory]) -> None:
    """Run until cancelled: one tick, then wait an hour."""
    while True:
        try:
            await cleanup_tick(session_factory_provider())
        except Exception:  # CancelledError is not an Exception, so cancelling still stops it
            logger.exception("auth_cleanup_failed")
        await _sleep(CLEANUP_INTERVAL.total_seconds())
