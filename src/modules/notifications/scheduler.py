"""The scheduler: reading reminders and clean-up, every 10 minutes (task N6).

docs/ARCHITECTURE.md 3.3. A tick has two short transactions, reminders and then clean-up, each
under `pg_try_advisory_xact_lock`, so only one process works at a time and another one skips; a
failing stage never blocks the other, and a failing period never blocks the other periods.

- Reminders: for every open period whose deadline is near (`reminder_due`: from
  READING_REMINDER_DAYS_BEFORE days before the local deadline day, 09:00-21:00 local time), the
  active members with `can_submit_readings` of apartments that miss a reading get a
  READING_DEADLINE notification, once per period: a period that already has reminder rows is
  done, also after a restart. The rows are inserted here, in the tick's transaction, rather than
  through a domain event: the scheduler belongs to this module, and the insert and the "done"
  marker (the rows themselves) must be one unit.
- Clean-up: deliveries older than NOTIFICATIONS_RETENTION_DAYS, revoked bindings older than that
  which no delivery refers to any more, and link codes and link attempts older than 24 hours.

Nothing personal is logged: only counts.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from opentelemetry import metrics
from sqlalchemy import delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.domain_events import SessionFactory
from src.modules.meters.schemas import DuePeriodRefDTO
from src.modules.meters.service import MetersService
from src.modules.notifications.models import ChannelBinding, Delivery, LinkAttempt, LinkCode
from src.modules.notifications.policies import (
    LINK_ROWS_RETENTION,
    SCHEDULER_INTERVAL,
    reading_deadline_key,
    reminder_due,
)
from src.modules.notifications.schemas import NotificationEventType, SubjectType
from src.modules.notifications.service import NotificationsSubscriber
from src.modules.property.service import PropertyService

logger = structlog.get_logger(__name__)
_meter = metrics.get_meter(__name__)
_reminded_periods = _meter.create_counter(
    "notifications.reminded_periods",
    description="Reading periods for which reading reminders were created",
)
_deleted_rows = _meter.create_counter(
    "notifications.deleted_rows",
    description="Old notification rows deleted by the scheduler, by table",
)

_sleep = asyncio.sleep  # tests replace it, never the global asyncio.sleep

# One lock for the whole scheduler, so ticks of several processes never overlap.
SCHEDULER_LOCK_KEY = "notif:scheduler"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TickResult:
    ran: bool  # False when another process held the lock
    reminded_periods: int = 0
    deleted: tuple[tuple[str, int], ...] = ()


class Scheduler:
    def __init__(
        self,
        session_factory_provider: Callable[[], SessionFactory],
        subscriber: NotificationsSubscriber,
        settings: Settings,
    ) -> None:
        # Asked on every tick, so tests can swap the factory after create_app.
        self._session_factory_provider = session_factory_provider
        self._subscriber = subscriber
        self._settings = settings
        self._timezone = ZoneInfo(settings.INSTANCE_TIMEZONE)

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self._session_factory_provider()()

    async def tick(self, now: datetime | None = None) -> TickResult:
        """One run; `now` is injectable for tests."""
        now = now or _now()
        reminded, deleted, ran = 0, (), False
        try:
            async with self._session() as session:
                if await _try_lock(session):
                    ran = True
                    reminded = await self._remind(session, now)
                    await session.commit()
        except Exception:  # the clean-up still runs
            logger.exception("notification_reminders_failed")
        async with self._session() as session:
            if await _try_lock(session):
                ran = True
                deleted = await self._clean_up(session, now)
                await session.commit()
        if not ran:
            return TickResult(ran=False)
        if reminded or any(count for _, count in deleted):
            logger.info(
                "notification_scheduler_tick",
                reminded_periods=reminded,
                **{f"deleted_{table}": count for table, count in deleted},
            )
        return TickResult(ran=True, reminded_periods=reminded, deleted=deleted)

    # --- Reading reminders ----------------------------------------------------------------------

    async def _remind(self, session: AsyncSession, now: datetime) -> int:
        days_before = self._settings.READING_REMINDER_DAYS_BEFORE
        properties = PropertyService(session)
        meters = MetersService(session, properties)
        # Every period whose deadline day starts within the lead time (one extra day covers the
        # local day boundary); reminder_due decides exactly.
        candidates = await meters.open_periods_due_within(
            now + timedelta(days=days_before + 1), now=now
        )
        reminded = 0
        for period in candidates:
            if not reminder_due(period.due_date, now, self._timezone, days_before):
                continue
            try:
                async with session.begin_nested():  # one broken period never blocks the others
                    reminded += await self._remind_period(session, meters, properties, period, now)
            except Exception:
                logger.exception("notification_reminder_failed", period_id=str(period.period_id))
        return reminded

    async def _remind_period(
        self,
        session: AsyncSession,
        meters: MetersService,
        properties: PropertyService,
        period: DuePeriodRefDTO,
        now: datetime,
    ) -> int:
        key = reading_deadline_key(period.period_id)
        marked = select(exists().where(Delivery.dedup_key == key))
        if await session.scalar(marked):
            return 0  # this period's reminders were created already
        apartments = await meters.apartments_missing_readings(period.period_id)
        recipients = await properties.reading_submitters(apartments)
        if not recipients:
            return 0  # nothing missing, or nobody to ask (checked again on the next tick)
        await self._subscriber.notify(
            session,
            recipients,
            event_type=NotificationEventType.READING_DEADLINE,
            dedup_key=key,
            subject=(SubjectType.PERIOD, period.period_id),
            params={
                "period_code": period.period_code,
                "due_date": period.due_date.astimezone(UTC).isoformat(),
            },
            now=now,
        )
        if not await session.scalar(marked):
            return 0  # everyone switched reminders off: no row, so no marker yet
        _reminded_periods.add(1)
        return 1

    # --- Clean-up -------------------------------------------------------------------------------

    async def _clean_up(self, session: AsyncSession, now: datetime) -> tuple[tuple[str, int], ...]:
        retention_start = now - timedelta(days=self._settings.NOTIFICATIONS_RETENTION_DAYS)
        link_start = now - LINK_ROWS_RETENTION
        statements = (
            ("deliveries", delete(Delivery).where(Delivery.created_at < retention_start)),
            (
                # After the deliveries, so the old rows that pointed at a binding are gone.
                "bindings",
                delete(ChannelBinding).where(
                    ChannelBinding.revoked_at < retention_start,
                    ~exists().where(Delivery.binding_id == ChannelBinding.id),
                ),
            ),
            ("link_codes", delete(LinkCode).where(LinkCode.created_at < link_start)),
            ("link_attempts", delete(LinkAttempt).where(LinkAttempt.attempted_at < link_start)),
        )
        deleted = []
        for table, statement in statements:
            result = await session.execute(statement.execution_options(synchronize_session=False))
            count = result.rowcount or 0
            deleted.append((table, count))
            if count:
                _deleted_rows.add(count, {"table": table})
        return tuple(deleted)


async def _try_lock(session: AsyncSession) -> bool:
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": SCHEDULER_LOCK_KEY},
    )
    return bool(locked)


async def run_scheduler_loop(scheduler: Scheduler) -> None:
    """Run until cancelled: one tick, then wait for the next one."""
    while True:
        try:
            await scheduler.tick()
        except Exception:  # CancelledError is not an Exception, so cancelling still stops the loop
            logger.exception("notification_scheduler_failed")
        await _sleep(SCHEDULER_INTERVAL.total_seconds())
