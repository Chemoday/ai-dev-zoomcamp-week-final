"""The dispatcher and the e-mail channel on a real PostgreSQL with an in-process SMTP server.

Demo mode is off and requests authenticate with a test JWT (owner decision: external channels run
nowhere yet and are accepted by these tests; docs/epics/E1_NOTIFICATIONS.md 10.10).
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.config import Settings, get_settings
from src.core.security import create_access_token
from src.main import create_app
from src.modules.communication.events import EmergencyAlertPublished
from src.modules.communication.schemas import PostCategory, TargetScope
from src.modules.notifications import dispatcher as dispatcher_module
from src.modules.notifications.dispatcher import Dispatcher, Outcome
from src.modules.notifications.models import Delivery, Preference
from src.modules.notifications.policies import MAX_ATTEMPTS
from src.modules.notifications.service import NotificationsSubscriber
from src.modules.property.models import Building, User
from src.modules.property.schemas import Locale
from src.plugins.registry import (
    DeliveryResult,
    OutgoingMessage,
    PluginRegistry,
    build_plugin_registry,
)
from tests.factories import create_building, create_user
from tests.notifications.conftest import NotifWorld
from tests.smtp_server import SmtpServer

JWT_SECRET = "dispatcher-tests-secret-at-least-32-bytes"
SITE = "https://kyu.example.test"
SENDER = "KU Sopruse 12 <noreply@kyu.example.test>"
TITLE = "Cold water off until 18:00"
BODY = "A riser in entrance 2 is being replaced."


@pytest.fixture
def email_settings(smtp_server: SmtpServer) -> Settings:
    return Settings(
        **{
            **get_settings().model_dump(),
            "DEMO_MODE_ENABLED": False,
            "JWT_SECRET_KEY": JWT_SECRET,
            "ENABLED_PLUGINS": "common.csv_export,common.email_smtp",
            "SMTP_HOST": smtp_server.host,
            "SMTP_PORT": smtp_server.port,
            "SMTP_TLS": "none",
            "SMTP_FROM": SENDER,
            "PUBLIC_BASE_URL": SITE,
            "NOTIFICATIONS_DISPATCHER_ENABLED": False,
        }
    )


@pytest.fixture
def app(span_exporter: InMemorySpanExporter, email_settings: Settings) -> FastAPI:
    """This module's app: demo mode off, the e-mail plugin on, pointed at the test server."""
    app = create_app(email_settings)
    app.dependency_overrides[get_settings] = lambda: email_settings
    return app


@pytest.fixture
def dispatcher(db_app: FastAPI) -> Dispatcher:
    return db_app.state.notifications_dispatcher


@pytest.fixture
async def mailboxes(world: NotifWorld, db_session: AsyncSession) -> dict[str, str]:
    """E-mail addresses for the people of the world (Mari Tamm has none)."""
    addresses = {
        "owner_14": world.owner_14.email,
        "board_15": "peeter.saar@example.com",
        "chairman": "chair@example.com",
        "accountant": "anna.kask@example.com",
        "inactive_16": "old.owner@example.com",
    }
    world.board_15.email = addresses["board_15"]
    world.chairman.email = addresses["chairman"]
    world.accountant.email = addresses["accountant"]
    world.inactive_16.email = addresses["inactive_16"]  # has an address, but is inactive
    world.owner_14.locale = Locale.EN
    await db_session.flush()
    return addresses


def bearer(user: User, settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, settings)}"}


async def publish(
    client: AsyncClient, author: User, settings: Settings, **overrides: Any
) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "post_type": "EMERGENCY",
        "category": "WATER_SHUTOFF",
        "title": TITLE,
        "body": BODY,
        "target_scope": "ENTRANCE",
        "target_entrance": 2,
        **overrides,
    }
    response = await client.post(
        "/api/v1/communication/posts", json=payload, headers=bearer(author, settings)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], response.headers["X-Trace-Id"]


async def email_rows(session: AsyncSession, **where: Any) -> list[Delivery]:
    query = (
        select(Delivery)
        .where(Delivery.channel == "email")
        .execution_options(populate_existing=True)
    )
    for name, value in where.items():
        query = query.where(getattr(Delivery, name) == value)
    return list(await session.scalars(query.order_by(Delivery.created_at, Delivery.id)))


async def period_email(
    session: AsyncSession, world: NotifWorld, *, created_at: datetime | None = None
) -> Delivery:
    """A PERIOD_REVIEW e-mail to the accountant (a non-emergency, so retries run to the end)."""
    now = created_at or datetime.now(UTC)
    row = Delivery(
        user_id=world.accountant.id,
        channel="email",
        event_type="PERIOD_REVIEW",
        dedup_key=f"period_review:{world.period.id}:{uuid.uuid4()}",
        subject_type="period",
        subject_id=world.period.id,
        params={"period_code": world.period.period_code},
        locale="en",
        priority=1,
        status="PENDING",
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


# --- The emergency e-mail -------------------------------------------------------------------------


async def test_an_alert_email_reaches_each_targeted_user_with_the_request_trace(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
    span_exporter: InMemorySpanExporter,
) -> None:
    _, trace_id = await publish(api_client, world.chairman, email_settings)

    pending = await email_rows(db_session)
    assert {row.user_id for row in pending} == {world.owner_14.id, world.board_15.id}
    assert all(row.status == "PENDING" and row.trace_id == trace_id for row in pending)

    assert await dispatcher.tick() == 2

    by_recipient = {
        tuple(rcpts): email
        for rcpts, email in zip(smtp_server.handler.recipients, smtp_server.messages, strict=True)
    }
    owner = by_recipient[(mailboxes["owner_14"],)]
    board = by_recipient[(mailboxes["board_15"],)]
    assert owner["Subject"] == f"Emergency: {TITLE}"  # Ivan chose English
    assert board["Subject"] == f"Avarii: {TITLE}"  # the instance default, Estonian
    text = owner.get_content().replace("\r\n", "\n")
    assert text.startswith(f"{BODY}\n\nAffected: entrance 2\n\n-- \n")
    assert SITE in text
    assert {owner["X-Trace-Id"], board["X-Trace-Id"]} == {trace_id}

    sent = await email_rows(db_session)
    assert all(
        row.status == "SENT" and row.sent_at and row.attempts == 1 and row.lease_until is None
        for row in sent
    )
    spans = [s for s in span_exporter.get_finished_spans() if s.name == "notifications.deliver"]
    assert len(spans) == 2
    assert {format(s.context.trace_id, "032x") for s in spans} == {trace_id}
    for span in spans:
        assert span.attributes["notification.status"] == "SENT"
        values = " ".join(str(v) for v in span.attributes.values())
        assert all(address not in values for address in mailboxes.values())
        assert TITLE not in values


async def test_users_without_an_address_and_the_author_get_no_email(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    db_session: AsyncSession,
) -> None:
    await publish(api_client, world.chairman, email_settings, target_scope="ALL_BUILDING",
                  target_entrance=None)  # fmt: skip

    recipients = {row.user_id for row in await email_rows(db_session)}
    assert world.tenant_2.id not in recipients, "no e-mail address"
    assert world.chairman.id not in recipients, "never the author"
    assert world.inactive_16.id not in recipients
    assert world.owner_14.id in recipients


async def test_a_resolved_email_follows_a_sent_alert_for_those_who_want_it(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    # "Resolved" by e-mail is off by default; Ivan switches it on, Peeter (board) keeps it off.
    response = await api_client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": [{"event_type": "EMERGENCY_RESOLVED", "channel": "email",
                               "enabled": True}]},
        headers=bearer(world.owner_14, email_settings),
    )  # fmt: skip
    assert response.status_code == 200, response.text
    post_id, _ = await publish(api_client, world.chairman, email_settings)
    await dispatcher.tick()
    smtp_server.messages.clear()
    smtp_server.handler.recipients.clear()

    response = await api_client.post(
        f"/api/v1/communication/emergencies/{post_id}/resolve",
        headers=bearer(world.chairman, email_settings),
    )
    assert response.status_code == 200, response.text
    resolved = await email_rows(db_session, event_type="EMERGENCY_RESOLVED")
    assert {row.user_id for row in resolved} == {world.owner_14.id}

    assert await dispatcher.tick() == 1
    [email] = smtp_server.messages
    assert smtp_server.handler.recipients == [[mailboxes["owner_14"]]]
    assert email["Subject"] == f"Resolved: {TITLE}"


async def test_an_alert_resolved_before_sending_is_skipped(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    post_id, _ = await publish(api_client, world.chairman, email_settings)
    await api_client.post(
        f"/api/v1/communication/emergencies/{post_id}/resolve",
        headers=bearer(world.chairman, email_settings),
    )

    await dispatcher.tick()

    rows = await email_rows(db_session)
    assert {(row.status, row.error_code) for row in rows} == {("SKIPPED", "POST_RESOLVED")}
    assert smtp_server.messages == []
    assert await email_rows(db_session, event_type="EMERGENCY_RESOLVED") == [], (
        "nobody got the alert by e-mail, so nobody gets 'resolved'"
    )


async def test_an_alert_processed_more_than_an_hour_late_stays_in_the_app(
    db_app: FastAPI, world: NotifWorld, mailboxes: dict[str, str], db_session: AsyncSession
) -> None:
    post_id, _ = await _stored_emergency(db_session, world)
    late = EmergencyAlertPublished(
        post_id=post_id,
        building_id=world.building.id,
        category=PostCategory.WATER_SHUTOFF,
        target_scope=TargetScope.ENTRANCE,
        target_entrance=2,
        target_apartment_ids=(),
        published_by_user_id=world.chairman.id,
        occurred_at=datetime.now(UTC) - timedelta(hours=2),
    )

    await db_app.state.event_bus.publish(late)

    in_app = await db_session.scalars(select(Delivery).where(Delivery.channel == "in_app"))
    assert {row.user_id for row in in_app} == {world.owner_14.id, world.board_15.id}
    assert await email_rows(db_session) == []


async def _stored_emergency(session: AsyncSession, world: NotifWorld) -> tuple[uuid.UUID, Any]:
    from src.modules.communication.models import Post
    from src.modules.communication.schemas import PostType

    post = Post(
        building_id=world.building.id,
        author_user_id=world.chairman.id,
        post_type=PostType.EMERGENCY,
        category=PostCategory.WATER_SHUTOFF,
        title=TITLE,
        body=BODY,
        target_scope=TargetScope.ENTRANCE,
        target_entrance=2,
        is_active=True,
    )
    session.add(post)
    await session.flush()
    return post.id, post


# --- Retries, failures and send-time checks -------------------------------------------------------


async def test_a_temporary_failure_retries_on_schedule_then_fails(
    db_app: FastAPI,
    world: NotifWorld,
    mailboxes: dict[str, str],
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    smtp_server.handler.data_reply = "451 4.3.0 Try again later"
    t0 = datetime.now(UTC)
    row = await period_email(db_session, world, created_at=t0)

    now = t0
    for attempt, wait in enumerate([1, 5, 30, 120], start=1):
        assert await dispatcher.tick(now) == 1
        [state] = await email_rows(db_session, id=row.id)
        assert (state.status, state.attempts, state.error_code) == (
            "PENDING",
            attempt,
            "SMTP_TEMPORARY",
        )
        assert state.next_attempt_at == now + timedelta(minutes=wait)
        assert state.lease_until is None
        assert await dispatcher.tick(state.next_attempt_at - timedelta(seconds=1)) == 0
        now = state.next_attempt_at

    assert await dispatcher.tick(now) == 1
    [state] = await email_rows(db_session, id=row.id)
    assert (state.status, state.attempts, state.error_code) == (
        "FAILED",
        MAX_ATTEMPTS,
        "SMTP_TEMPORARY",
    )
    assert await dispatcher.tick(now + timedelta(days=1)) == 0


async def test_an_emergency_that_misses_its_hour_is_skipped_not_sent_late(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    await publish(api_client, world.chairman, email_settings)
    smtp_server.handler.data_reply = "451 4.3.0 Try again later"
    start = datetime.now(UTC)
    await dispatcher.tick(start)  # attempt 1 fails
    smtp_server.handler.data_reply = None

    await dispatcher.tick(start + timedelta(hours=1, minutes=1))

    rows = await email_rows(db_session)
    assert {(row.status, row.error_code) for row in rows} == {("SKIPPED", "EMERGENCY_TOO_OLD")}
    assert smtp_server.messages == []


async def test_a_rejected_address_fails_at_once(
    world: NotifWorld,
    mailboxes: dict[str, str],
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    smtp_server.handler.rcpt_reply = "550 5.1.1 No such user"
    row = await period_email(db_session, world)

    await dispatcher.tick()

    [state] = await email_rows(db_session, id=row.id)
    assert (state.status, state.attempts, state.error_code) == ("FAILED", 1, "SMTP_REJECTED")


@pytest.mark.parametrize("case", ["inactive", "no_email", "preference_off"])
async def test_send_time_checks_skip_without_sending(
    world: NotifWorld,
    mailboxes: dict[str, str],
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
    case: str,
) -> None:
    row = await period_email(db_session, world)
    if case == "inactive":
        world.accountant.is_active = False
    elif case == "no_email":
        world.accountant.email = None
    else:
        db_session.add(
            Preference(
                user_id=world.accountant.id,
                event_type="PERIOD_REVIEW",
                channel="email",
                enabled=False,
            )
        )
    await db_session.flush()

    await dispatcher.tick()

    [state] = await email_rows(db_session, id=row.id)
    expected = {"inactive": "USER_INACTIVE", "no_email": "NO_EMAIL"}.get(case, "PREFERENCE_OFF")
    assert (state.status, state.error_code) == ("SKIPPED", expected)
    assert smtp_server.messages == []


async def test_demo_mode_and_a_disabled_plugin_send_nothing(
    db_app: FastAPI,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    factory = lambda: db_app.state.notifications_session_factory  # noqa: E731
    demo = Settings(**{**email_settings.model_dump(), "DEMO_MODE_ENABLED": True})
    demo_row = await period_email(db_session, world)
    await Dispatcher(factory, build_plugin_registry(email_settings), demo).tick()

    no_plugin_row = await period_email(db_session, world)
    await Dispatcher(factory, PluginRegistry(email_settings), email_settings).tick()

    [demo_state] = await email_rows(db_session, id=demo_row.id)
    [plugin_state] = await email_rows(db_session, id=no_plugin_row.id)
    assert (demo_state.status, demo_state.error_code) == ("SKIPPED", "DEMO_MODE")
    assert (plugin_state.status, plugin_state.error_code) == ("SKIPPED", "CHANNEL_NOT_ENABLED")
    assert smtp_server.messages == []


async def test_demo_mode_creates_no_email_rows(
    db_app: FastAPI,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    db_session: AsyncSession,
) -> None:
    post_id, _ = await _stored_emergency(db_session, world)
    subscriber = NotificationsSubscriber(
        lambda: db_app.state.notifications_session_factory,
        "et",
        demo_mode=True,
        plugins=build_plugin_registry(email_settings),
    )
    await subscriber.on_emergency_published(
        EmergencyAlertPublished(
            post_id=post_id,
            building_id=world.building.id,
            category=PostCategory.WATER_SHUTOFF,
            target_scope=TargetScope.ENTRANCE,
            target_entrance=2,
            target_apartment_ids=(),
            published_by_user_id=world.chairman.id,
        )
    )

    assert await email_rows(db_session) == []
    in_app = await db_session.scalars(select(Delivery).where(Delivery.channel == "in_app"))
    assert {row.user_id for row in in_app} == {world.owner_14.id, world.board_15.id}


# --- Leases --------------------------------------------------------------------------------------


async def test_an_expired_lease_is_claimed_again_and_a_stale_result_is_dropped(
    db_app: FastAPI,
    world: NotifWorld,
    mailboxes: dict[str, str],
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    row = await period_email(db_session, world)
    now = datetime.now(UTC)
    async with db_app.state.notifications_session_factory() as session:
        [first] = await dispatcher._claim(session, now)
        await session.commit()
    # The first worker died; after the lease another one claims the row again (at least once).
    async with db_app.state.notifications_session_factory() as session:
        [second] = await dispatcher._claim(session, now + timedelta(minutes=6))
        await session.commit()
    assert (first.id, first.attempts, second.attempts) == (row.id, 1, 2)

    async with db_app.state.notifications_session_factory() as session:
        await dispatcher._record(session, first, Outcome("SENT"), now)  # the late first worker
        await session.commit()

    [state] = await email_rows(db_session, id=row.id)
    assert (state.status, state.attempts) == ("SENDING", 2), "the stale result changed nothing"


async def test_an_expired_lease_at_the_last_attempt_fails(
    world: NotifWorld,
    mailboxes: dict[str, str],
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    row = await period_email(db_session, world)
    now = datetime.now(UTC)
    await db_session.execute(
        update(Delivery)
        .where(Delivery.id == row.id)
        .values(status="SENDING", attempts=MAX_ATTEMPTS, lease_until=now - timedelta(seconds=1))
    )

    assert await dispatcher.tick(now) == 0

    [state] = await email_rows(db_session, id=row.id)
    assert (state.status, state.error_code, state.lease_until) == ("FAILED", "LEASE_EXPIRED", None)


async def test_emergencies_are_sent_before_other_messages(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
) -> None:
    earlier = datetime.now(UTC) - timedelta(minutes=5)
    for _ in range(10):  # a full batch of older, lower-priority messages
        await period_email(db_session, world, created_at=earlier)
    await publish(api_client, world.chairman, email_settings)

    await dispatcher.tick()

    subjects = [email["Subject"] for email in smtp_server.messages]
    assert set(subjects[:2]) == {f"Emergency: {TITLE}", f"Avarii: {TITLE}"}, "priority 0 first"


# --- Startup and privacy -------------------------------------------------------------------------


def test_the_app_refuses_to_start_with_email_but_no_site_url(email_settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        create_app(Settings(**{**email_settings.model_dump(), "PUBLIC_BASE_URL": ""}))


async def test_failures_log_no_address_and_no_text(
    world: NotifWorld,
    mailboxes: dict[str, str],
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
    log_capture,
) -> None:
    smtp_server.handler.data_reply = "451 4.3.0 Try again later"
    await period_email(db_session, world)

    await dispatcher.tick()

    ours = [r for r in log_capture.records if r.get("logger", "").startswith("src.")]
    failed = [r for r in ours if r["event"] == "notification_delivery_failed"]
    assert failed and failed[0]["error_code"] == "SMTP_TEMPORARY"
    text = json.dumps(ours)
    assert mailboxes["accountant"] not in text
    assert "ready for review" not in text, "no message text in the logs"


async def test_an_emergency_failure_logs_neither_title_nor_body(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    smtp_server: SmtpServer,
    log_capture,
) -> None:
    await publish(api_client, world.chairman, email_settings)
    smtp_server.handler.data_reply = "451 4.3.0 Try again later"

    await dispatcher.tick()

    ours = json.dumps([r for r in log_capture.records if r.get("logger", "").startswith("src.")])
    assert "notification_delivery_failed" in ours
    assert TITLE not in ours
    assert BODY not in ours


# --- Isolation and timeouts ----------------------------------------------------------------------


async def test_one_broken_row_does_not_stop_its_batch(
    api_client: AsyncClient,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    smtp_server: SmtpServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_compose = dispatcher_module.compose

    def compose(event_type, *args, **kwargs):
        if event_type == "PERIOD_REVIEW":
            raise KeyError("period_code")  # e.g. a bug in a template
        return real_compose(event_type, *args, **kwargs)

    monkeypatch.setattr(dispatcher_module, "compose", compose)
    broken = await period_email(db_session, world)
    await publish(api_client, world.chairman, email_settings)

    assert await dispatcher.tick() == 3

    [state] = await email_rows(db_session, id=broken.id)
    assert (state.status, state.error_code, state.attempts) == ("PENDING", "PREPARE_ERROR", 1)
    assert len(smtp_server.messages) == 2, "the emergency still went out"


class SlowChannel:
    name = "email"

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        await asyncio.sleep(5)
        return DeliveryResult(ok=True)


async def test_a_send_that_hangs_is_cut_off_and_retried(
    db_app: FastAPI,
    world: NotifWorld,
    mailboxes: dict[str, str],
    email_settings: Settings,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dispatcher_module, "SEND_TIMEOUT_SECONDS", 0.1)
    plugins = PluginRegistry(email_settings)
    plugins.register_channel(SlowChannel())
    row = await period_email(db_session, world)

    await Dispatcher(lambda: db_app.state.notifications_session_factory, plugins,
                     email_settings).tick()  # fmt: skip

    [state] = await email_rows(db_session, id=row.id)
    assert (state.status, state.error_code) == ("PENDING", "CHANNEL_TIMEOUT")


async def test_two_workers_never_claim_the_same_row(
    db_engine: AsyncEngine, email_settings: Settings
) -> None:
    """Separate connections, committed data: FOR UPDATE SKIP LOCKED really skips."""
    async with AsyncSession(db_engine, expire_on_commit=False) as setup:
        building = await create_building(setup, name="KU Skip Locked 1")
        user = await create_user(setup, building, "Lea Lukk", email="lea@example.com")
        now = datetime.now(UTC)
        for n in range(3):
            setup.add(
                Delivery(
                    user_id=user.id, channel="email", event_type="PERIOD_REVIEW",
                    dedup_key=f"skip-locked:{n}", subject_type="period",
                    subject_id=uuid.uuid4(), params={"period_code": "2026-11"}, locale="en",
                    priority=1, status="PENDING", attempts=0, next_attempt_at=now,
                    created_at=now,
                )
            )  # fmt: skip
        await setup.commit()
    worker = Dispatcher(lambda: None, PluginRegistry(email_settings), email_settings)
    try:
        async with AsyncSession(db_engine) as first, AsyncSession(db_engine) as second:
            claimed_first = await worker._claim(first, now)  # holds its row locks
            claimed_second = await worker._claim(second, now)
            ids_first = {row.id for row in claimed_first}
            ids_second = {row.id for row in claimed_second}
            await first.rollback()
            await second.rollback()
        assert len(ids_first) == 3
        assert ids_second == set(), "rows locked by the first worker are skipped, not shared"
    finally:
        async with AsyncSession(db_engine) as cleanup:
            await cleanup.execute(delete(Delivery).where(Delivery.user_id == user.id))
            await cleanup.execute(delete(User).where(User.id == user.id))
            await cleanup.execute(delete(Building).where(Building.id == building.id))
            await cleanup.commit()
