"""The scheduler on a real PostgreSQL: reading reminders and clean-up (task N6).

The world's period "2026-10" is due at the end of 4 November 2026 in Tallinn (21:59:59 UTC), so
with the default lead time of 2 days reminders may go out on 2-4 November, 09:00-21:00 local time
(UTC+2 in November).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.config import Settings, get_settings
from src.main import create_app
from src.modules.meters.schemas import PeriodStatus
from src.modules.notifications import scheduler as scheduler_module
from src.modules.notifications.dispatcher import Dispatcher
from src.modules.notifications.models import (
    ChannelBinding,
    Delivery,
    LinkAttempt,
    LinkCode,
    Preference,
)
from src.modules.notifications.scheduler import SCHEDULER_LOCK_KEY, Scheduler, run_scheduler_loop
from src.modules.property.models import Apartment, ApartmentMembership
from src.modules.property.schemas import Locale
from tests.factories import (
    create_apartment,
    create_meter,
    create_period,
    create_reading,
    create_user,
)
from tests.notifications.conftest import NotifWorld
from tests.smtp_server import SmtpServer

SITE = "https://kyu.example.test"
SENDER = "KU Sopruse 12 <noreply@kyu.example.test>"


def tallinn(day: int, hour: int, minute: int = 0) -> datetime:
    """A November 2026 moment in Tallinn (UTC+2), as UTC."""
    return datetime(2026, 11, day, hour - 2, minute, tzinfo=UTC)


IN_WINDOW = tallinn(2, 10)


@pytest.fixture
def scheduler_settings(smtp_server: SmtpServer) -> Settings:
    return Settings(
        **{
            **get_settings().model_dump(),
            "DEMO_MODE_ENABLED": False,
            "ENABLED_PLUGINS": "common.csv_export,common.email_smtp",
            "SMTP_HOST": smtp_server.host,
            "SMTP_PORT": smtp_server.port,
            "SMTP_TLS": "none",
            "SMTP_FROM": SENDER,
            "PUBLIC_BASE_URL": SITE,
            "NOTIFICATIONS_DISPATCHER_ENABLED": False,
            "NOTIFICATIONS_SCHEDULER_ENABLED": False,
        }
    )


@pytest.fixture
def app(span_exporter: InMemorySpanExporter, scheduler_settings: Settings) -> FastAPI:
    app = create_app(scheduler_settings)
    app.dependency_overrides[get_settings] = lambda: scheduler_settings
    return app


@pytest.fixture
def scheduler(db_app: FastAPI) -> Scheduler:
    return db_app.state.notifications_scheduler


@pytest.fixture
def dispatcher(db_app: FastAPI) -> Dispatcher:
    return db_app.state.notifications_dispatcher


@pytest.fixture
async def meters(world: NotifWorld, db_session: AsyncSession) -> NotifWorld:
    """Meters in apartments 14, 2 and 15 and the inactive owner's 16; apartment 2 has submitted."""
    apt_16 = await db_session.get_one(
        Apartment,
        await db_session.scalar(
            select(ApartmentMembership.apartment_id).where(
                ApartmentMembership.user_id == world.inactive_16.id
            )
        ),
    )
    for apartment in (world.apt_14, world.apt_15, apt_16):
        await create_meter(db_session, world.building, apartment)
    await create_meter(db_session, world.building, None)  # the building's master meter
    meter_2 = await create_meter(db_session, world.building, world.apt_2)
    await create_reading(db_session, meter_2, world.period, Decimal("5.000"), Decimal("0.000"))
    world.owner_14.email = "ivan@example.com"
    world.owner_14.locale = Locale.EN
    await db_session.flush()
    return world


async def reminders(session: AsyncSession, channel: str = "in_app") -> list[Delivery]:
    rows = await session.scalars(
        select(Delivery)
        .where(Delivery.event_type == "READING_DEADLINE", Delivery.channel == channel)
        .order_by(Delivery.created_at, Delivery.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


# --- Reminders ----------------------------------------------------------------------------------


async def test_members_of_apartments_missing_a_reading_are_reminded_once(
    scheduler: Scheduler, meters: NotifWorld, db_session: AsyncSession
) -> None:
    result = await scheduler.tick(now=IN_WINDOW)

    assert result.ran and result.reminded_periods == 1
    rows = await reminders(db_session)
    # Apartment 2 submitted, the inactive owner of 16 and the master meter are not reminded.
    assert {r.user_id for r in rows} == {meters.owner_14.id, meters.board_15.id}
    assert all(r.status == "SENT" for r in rows)
    assert rows[0].subject_type == "period" and rows[0].subject_id == meters.period.id
    assert rows[0].params == {
        "period_code": "2026-10",
        "due_date": "2026-11-04T21:59:59+00:00",
    }
    [email] = await reminders(db_session, "email")  # the board member has no address
    assert email.user_id == meters.owner_14.id and email.status == "PENDING"

    again = await scheduler.tick(now=IN_WINDOW + timedelta(hours=3))
    assert again.reminded_periods == 0, "once per period, also on later ticks or after a restart"
    assert len(await reminders(db_session)) == 2


async def test_a_member_without_the_right_to_submit_is_not_reminded(
    scheduler: Scheduler, meters: NotifWorld, db_session: AsyncSession
) -> None:
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.user_id == meters.board_15.id)
        .values(can_submit_readings=False)
    )
    second = await create_user(db_session, meters.building, "Kati Saar")
    db_session.add(
        ApartmentMembership(
            apartment_id=meters.apt_15.id,
            user_id=second.id,
            relation_type="OWNER",
            can_vote=True,
            privacy_display_mode="APARTMENT_ONLY",
        )
    )
    await db_session.flush()

    await scheduler.tick(now=IN_WINDOW)

    assert {r.user_id for r in await reminders(db_session)} == {meters.owner_14.id, second.id}


@pytest.mark.parametrize(
    "now",
    [
        tallinn(1, 12),  # three days before the deadline day: too early
        tallinn(2, 8, 59),  # before 09:00 local
        tallinn(2, 21),  # 21:00 local and later
        tallinn(4, 23, 30),  # the deadline day, but at night
        tallinn(5, 10),  # after the deadline
    ],
)
async def test_no_reminder_outside_the_window(
    scheduler: Scheduler, meters: NotifWorld, db_session: AsyncSession, now: datetime
) -> None:
    result = await scheduler.tick(now=now)

    assert result.ran and result.reminded_periods == 0
    assert await reminders(db_session) == []


async def test_a_missed_window_moves_to_the_next_one(
    scheduler: Scheduler, meters: NotifWorld, db_session: AsyncSession
) -> None:
    # The app was down on 2 and 3 November; the last day still gets the reminder.
    result = await scheduler.tick(now=tallinn(4, 9, 5))

    assert result.reminded_periods == 1


async def test_only_open_periods_are_reminded(
    scheduler: Scheduler, meters: NotifWorld, db_session: AsyncSession
) -> None:
    meters.period.status = PeriodStatus.REVIEW_BY_BOARD
    await db_session.flush()

    assert (await scheduler.tick(now=IN_WINDOW)).reminded_periods == 0


async def test_a_period_with_everything_submitted_is_checked_again_later(
    scheduler: Scheduler, world: NotifWorld, db_session: AsyncSession
) -> None:
    assert (await scheduler.tick(now=IN_WINDOW)).reminded_periods == 0  # no meters yet

    await create_meter(db_session, world.building, world.apt_14)
    result = await scheduler.tick(now=IN_WINDOW + timedelta(minutes=10))

    assert result.reminded_periods == 1
    assert [r.user_id for r in await reminders(db_session)] == [world.owner_14.id]


async def test_a_reminder_retried_after_the_deadline_is_skipped(
    scheduler: Scheduler,
    dispatcher: Dispatcher,
    meters: NotifWorld,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    await scheduler.tick(now=IN_WINDOW)

    await dispatcher.tick(now=datetime(2026, 11, 4, 22, 0, tzinfo=UTC))

    [email] = await reminders(db_session, "email")
    assert (email.status, email.error_code) == ("SKIPPED", "DEADLINE_PASSED")
    assert smtp_server.messages == []


async def test_the_reminder_e_mail(
    scheduler: Scheduler,
    dispatcher: Dispatcher,
    meters: NotifWorld,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    await scheduler.tick(now=IN_WINDOW)

    await dispatcher.tick(now=IN_WINDOW + timedelta(seconds=5))

    [message] = smtp_server.messages
    assert message["Subject"] == "Meter readings for 2026-10 are due"
    assert "by 04.11.2026" in message.get_content()


async def test_a_second_process_skips_the_tick(
    scheduler: Scheduler, meters: NotifWorld, db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    async with db_engine.connect() as other:
        await other.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": SCHEDULER_LOCK_KEY}
        )
        try:
            result = await scheduler.tick(now=IN_WINDOW)
        finally:
            await other.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": SCHEDULER_LOCK_KEY},
            )

    assert result.ran is False
    assert await reminders(db_session) == []


# --- Clean-up -----------------------------------------------------------------------------------


def delivery(user_id: uuid.UUID, created_at: datetime, **values: object) -> Delivery:
    return Delivery(
        **{
            "user_id": user_id,
            "channel": "in_app",
            "event_type": "PERIOD_REVIEW",
            "dedup_key": f"period_review:{uuid.uuid4()}:{uuid.uuid4()}",
            "subject_type": "period",
            "subject_id": uuid.uuid4(),
            "params": {},
            "locale": "en",
            "priority": 1,
            "status": "SENT",
            "attempts": 0,
            "next_attempt_at": created_at,
            "created_at": created_at,
            "sent_at": created_at,
            **values,
        }
    )


def binding(user_id: uuid.UUID, *, revoked_at: datetime | None) -> ChannelBinding:
    return ChannelBinding(
        user_id=user_id,
        channel="telegram",
        config=None if revoked_at else {"chat_id": 1},
        address_hash=uuid.uuid4().hex * 2,
        label="Telegram",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        revoked_at=revoked_at,
    )


async def test_old_rows_are_deleted(
    scheduler: Scheduler, world: NotifWorld, db_session: AsyncSession
) -> None:
    now = IN_WINDOW
    user = world.owner_14.id
    old, recent = now - timedelta(days=181), now - timedelta(days=179)
    old_delivery, new_delivery = delivery(user, old), delivery(user, recent)
    gone = binding(user, revoked_at=old)  # revoked long ago, nothing refers to it
    still_referenced = binding(user, revoked_at=old)
    active = binding(world.tenant_2.id, revoked_at=None)
    db_session.add_all([old_delivery, new_delivery, gone, still_referenced, active])
    await db_session.flush()
    db_session.add(
        delivery(user, recent, channel="telegram", binding_id=still_referenced.id,
                 status="SKIPPED", error_code="BINDING_REVOKED", sent_at=None)
    )  # fmt: skip
    day_old = now - timedelta(hours=25)
    db_session.add_all(
        [
            LinkCode(user_id=user, code_hash="a" * 64, created_at=day_old,
                     expires_at=day_old + timedelta(minutes=10)),
            LinkCode(user_id=user, code_hash="b" * 64, created_at=now,
                     expires_at=now + timedelta(minutes=10)),
            LinkAttempt(address_hash="c" * 64, attempted_at=day_old, succeeded=False),
            LinkAttempt(address_hash="c" * 64, attempted_at=now, succeeded=True),
        ]
    )  # fmt: skip
    await db_session.flush()
    ids = {"old": old_delivery.id, "new": new_delivery.id}

    result = await scheduler.tick(now=now)

    assert dict(result.deleted) == {
        "deliveries": 1,
        "bindings": 1,
        "link_codes": 1,
        "link_attempts": 1,
    }
    remaining = set(await db_session.scalars(select(Delivery.id)))
    assert ids["new"] in remaining and ids["old"] not in remaining
    bindings = set(await db_session.scalars(select(ChannelBinding.id)))
    assert bindings == {still_referenced.id, active.id}
    assert list(await db_session.scalars(select(LinkCode.code_hash))) == ["b" * 64]
    assert list(await db_session.scalars(select(LinkAttempt.succeeded))) == [True]


async def test_the_retention_is_a_setting(
    db_app: FastAPI, world: NotifWorld, db_session: AsyncSession, scheduler_settings: Settings
) -> None:
    settings = Settings(**{**scheduler_settings.model_dump(), "NOTIFICATIONS_RETENTION_DAYS": 30})
    scheduler = Scheduler(
        lambda: db_app.state.notifications_session_factory,
        db_app.state.notifications_scheduler._subscriber,
        settings,
    )
    db_session.add(delivery(world.owner_14.id, IN_WINDOW - timedelta(days=31)))
    await db_session.flush()

    result = await scheduler.tick(now=IN_WINDOW)

    assert dict(result.deleted)["deliveries"] == 1


async def test_a_new_apartment_without_members_reminds_nobody(
    scheduler: Scheduler, world: NotifWorld, db_session: AsyncSession
) -> None:
    empty = await create_apartment(db_session, world.building, "99", entrance=1, floor=9)
    await create_meter(db_session, world.building, empty)

    result = await scheduler.tick(now=IN_WINDOW)

    assert result.reminded_periods == 0
    assert await reminders(db_session) == []


async def test_a_reminder_due_at_night_waits_for_nine_without_spending_an_attempt(
    scheduler: Scheduler,
    dispatcher: Dispatcher,
    meters: NotifWorld,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    await scheduler.tick(now=tallinn(2, 20, 59))  # created just before the end of the window

    await dispatcher.tick(now=tallinn(2, 21, 30))  # e.g. a retry or a dispatcher outage

    [email] = await reminders(db_session, "email")
    assert (email.status, email.attempts, email.error_code) == ("PENDING", 0, None)
    assert email.next_attempt_at == tallinn(3, 9)
    assert smtp_server.messages == []

    await dispatcher.tick(now=tallinn(3, 9, 1))
    [email] = await reminders(db_session, "email")
    assert (email.status, email.attempts) == ("SENT", 1)


async def test_a_night_with_no_window_left_before_the_deadline_skips(
    scheduler: Scheduler, dispatcher: Dispatcher, meters: NotifWorld, db_session: AsyncSession
) -> None:
    await scheduler.tick(now=tallinn(4, 20, 59))

    await dispatcher.tick(now=tallinn(4, 22))

    [email] = await reminders(db_session, "email")
    assert (email.status, email.error_code) == ("SKIPPED", "DEADLINE_PASSED")


async def test_with_every_channel_switched_off_nothing_is_stored_and_the_period_stays_open(
    scheduler: Scheduler, meters: NotifWorld, db_session: AsyncSession
) -> None:
    for user in (meters.owner_14, meters.board_15):
        for channel in ("in_app", "email"):
            db_session.add(
                Preference(
                    user_id=user.id,
                    event_type="READING_DEADLINE",
                    channel=channel,
                    enabled=False,
                    updated_at=IN_WINDOW,
                )
            )
    await db_session.flush()

    assert (await scheduler.tick(now=IN_WINDOW)).reminded_periods == 0
    assert await reminders(db_session) == []
    # No row is the marker yet, so a member who switches reminders on later is still reminded.
    await db_session.execute(
        delete(Preference).where(
            Preference.user_id == meters.owner_14.id, Preference.channel == "in_app"
        )
    )
    await scheduler.tick(now=IN_WINDOW + timedelta(minutes=10))
    assert [r.user_id for r in await reminders(db_session)] == [meters.owner_14.id]


async def test_an_inactive_meter_needs_no_reading(
    scheduler: Scheduler, world: NotifWorld, db_session: AsyncSession
) -> None:
    meter = await create_meter(db_session, world.building, world.apt_14)
    meter.is_active = False
    await db_session.flush()

    assert (await scheduler.tick(now=IN_WINDOW)).reminded_periods == 0


async def test_a_failing_reminder_stage_does_not_stop_the_clean_up(
    scheduler: Scheduler, meters: NotifWorld, monkeypatch: pytest.MonkeyPatch, log_capture
) -> None:
    async def broken(*args: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "_remind", broken)

    result = await scheduler.tick(now=IN_WINDOW)

    assert result.ran and dict(result.deleted).keys() == {
        "deliveries",
        "bindings",
        "link_codes",
        "link_attempts",
    }
    assert "notification_reminders_failed" in log_capture.raw


async def test_the_loop_survives_a_failing_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Failing:
        async def tick(self) -> None:
            calls.append("tick")
            raise RuntimeError("boom")

    async def stop_after_first_sleep(seconds: float) -> None:
        calls.append(f"sleep {seconds:.0f}")
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler_module, "_sleep", stop_after_first_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_scheduler_loop(Failing())  # type: ignore[arg-type]

    assert calls == ["tick", "sleep 600"]


async def test_one_broken_period_does_not_block_the_others(
    scheduler: Scheduler,
    meters: NotifWorld,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    log_capture,
) -> None:
    other = await create_period(db_session, meters.building, period_code="2026-09")
    other.due_date = meters.period.due_date - timedelta(seconds=1)  # handled first
    await db_session.flush()
    original = Scheduler._remind_period

    async def broken_for_other(self, session, meters_service, properties, period, now):  # noqa: ANN001
        if period.period_id == other.id:
            raise RuntimeError("boom")
        return await original(self, session, meters_service, properties, period, now)

    monkeypatch.setattr(Scheduler, "_remind_period", broken_for_other)

    result = await scheduler.tick(now=IN_WINDOW)

    assert result.reminded_periods == 1
    assert {r.subject_id for r in await reminders(db_session)} == {meters.period.id}
    assert "notification_reminder_failed" in log_capture.raw
