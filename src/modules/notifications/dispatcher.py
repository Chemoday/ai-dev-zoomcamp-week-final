"""The dispatcher: sends due external deliveries through channel plugins (task N3).

Protocol (docs/ARCHITECTURE.md 3.3):
1. a short transaction claims up to DISPATCH_BATCH_SIZE due rows with FOR UPDATE SKIP LOCKED and
   a lease (status SENDING, attempts + 1); expired leases at the last attempt become FAILED;
2. send-time checks and rendering read in a second short session; the sends then run one after
   another with no transaction open;
3. a third transaction records each result, fenced on status and attempts, so a worker that lost
   its lease cannot overwrite the row another worker took over.
Delivery is at least once: a crash after a send but before step 3 sends that message again.

No names, addresses or texts are logged or put on spans (docs/ARCHITECTURE.md 3.6).
"""

import asyncio
import random
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.domain_events import SessionFactory
from src.modules.communication.service import CommunicationService
from src.modules.notifications.messages import compose, default_catalog
from src.modules.notifications.models import ChannelBinding, Delivery
from src.modules.notifications.policies import (
    CHANNEL_PLUGINS,
    DISPATCH_BATCH_SIZE,
    EMERGENCY_MAX_AGE,
    LEASE_DURATION,
    MAX_ATTEMPTS,
    MIN_HASH_SECRET_BYTES,
    OTHER_MESSAGE_TTL,
    emergency_too_old,
    is_enabled,
    is_mandatory,
    retry_delay,
)
from src.modules.notifications.schemas import (
    Channel,
    DeliveryStatus,
    NotificationEventType,
    SubjectType,
)
from src.modules.notifications.service import stored_preferences
from src.modules.property.service import PropertyService
from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)
_meter = metrics.get_meter(__name__)
_deliveries = _meter.create_counter(
    "notifications.deliveries",
    description="Outcomes of external notification deliveries (sent, failed, skipped, retry)",
)
_revoked_bindings = _meter.create_counter(
    "notifications.revoked_bindings",
    description="Chats and devices unlinked because the provider reported them gone",
)

DISPATCH_INTERVAL_SECONDS = 5.0
# One send as a whole (a plugin's own timeout is per protocol step), so a batch fits in the lease.
SEND_TIMEOUT_SECONDS = 10.0


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Claimed:
    """A claimed delivery row, copied out of the claiming session."""

    id: uuid.UUID
    user_id: uuid.UUID
    channel: Channel
    event_type: NotificationEventType
    subject_type: SubjectType
    subject_id: uuid.UUID
    params: dict[str, Any]
    locale: str
    priority: int
    attempts: int  # after the claim's increment: the fence of the result update
    created_at: datetime
    trace_id: str | None
    binding_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Outcome:
    status: str  # "SENT", "FAILED", "SKIPPED" or "RETRY"
    error_code: str | None = None
    revoke_binding: bool = False  # e.g. the user blocked the bot


# External channels the dispatcher can send on (mobile push: N7).
_SENDABLE = (Channel.EMAIL, Channel.TELEGRAM, Channel.WEBPUSH)


@dataclass
class _Prepared:
    row: Claimed
    message: OutgoingMessage | None = None
    outcome: Outcome | None = field(default=None)  # set when the row is skipped before sending


def validate_channel_settings(settings: Settings, plugins: PluginRegistry) -> None:
    """Refuse to start with an external channel enabled but no site to link to, or with a
    broken messages.csv (loaded now, not on the first send)."""
    if not settings.NOTIFICATIONS_ENABLED:
        return
    enabled = [name for name in CHANNEL_PLUGINS.values() if plugins.is_enabled(name)]
    if not enabled:
        return
    if not settings.PUBLIC_BASE_URL:
        raise RuntimeError(f"PUBLIC_BASE_URL is required with {', '.join(enabled)}")
    hashing = [name for name in enabled if name in ("common.telegram", "common.webpush")]
    secret = settings.NOTIFICATIONS_HASH_SECRET.get_secret_value().encode()
    if hashing and len(secret) < MIN_HASH_SECRET_BYTES:
        raise RuntimeError(
            f"NOTIFICATIONS_HASH_SECRET of at least {MIN_HASH_SECRET_BYTES} bytes is required "
            f"with {', '.join(hashing)}"
        )
    default_catalog()


def _parent_context(trace_id: str | None) -> Context | None:
    """A context inside the trace of the request that caused the event, so the send span shows up
    in that trace. Only the trace id is stored, so the parent span id is a stand-in."""
    if not trace_id:
        return None
    try:
        trace_number = int(trace_id, 16)
    except ValueError:
        return None
    parent = SpanContext(
        trace_id=trace_number,
        span_id=random.getrandbits(64) or 1,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(parent))


def _collapse_key(row: Claimed) -> str:
    if row.subject_type == SubjectType.POST:
        return f"post:{row.subject_id}"
    return f"{row.event_type.value}:{row.subject_id}"


class Dispatcher:
    def __init__(
        self,
        session_factory_provider: Callable[[], SessionFactory],
        plugins: PluginRegistry,
        settings: Settings,
    ) -> None:
        # Asked on every tick, so tests can swap the factory after create_app.
        self._session_factory_provider = session_factory_provider
        self._plugins = plugins
        self._settings = settings

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self._session_factory_provider()()

    async def tick(self, now: datetime | None = None) -> int:
        """Claim, send and record one batch; returns how many rows it handled.

        `now` is injectable for tests; without it every step reads the clock itself.
        """
        clock = (lambda: now) if now else _now
        async with self._session() as session:
            await self._fail_expired_leases(session, clock())
            claimed = await self._claim(session, clock())
            await session.commit()
        if not claimed:
            return 0
        # Read only; the session is closed before sending, so no transaction stays open.
        async with self._session() as session:
            prepared = [await self._prepare_isolated(session, row, clock()) for row in claimed]
        results = [(item.row, item.outcome or await self._send(item)) for item in prepared]
        # One short transaction per result, so a failing update never resends the whole batch.
        for row, outcome in results:
            try:
                async with self._session() as session:
                    await self._record(session, row, outcome, clock())
                    await session.commit()
            except SQLAlchemyError:
                logger.exception("notification_record_failed", delivery_id=str(row.id))
        return len(claimed)

    # --- Step 1: claim --------------------------------------------------------------------------

    async def _fail_expired_leases(self, session: AsyncSession, now: datetime) -> None:
        expired = select(Delivery.id, Delivery.channel).where(
            Delivery.status == DeliveryStatus.SENDING,
            Delivery.lease_until < now,
            Delivery.attempts >= MAX_ATTEMPTS,
        )
        rows = (await session.execute(expired.with_for_update(skip_locked=True))).all()
        ids = [delivery_id for delivery_id, _ in rows]
        if ids:
            await session.execute(
                update(Delivery)
                .where(Delivery.id.in_(ids))
                .values(status=DeliveryStatus.FAILED, error_code="LEASE_EXPIRED", lease_until=None)
            )
            for _, channel in rows:
                _deliveries.add(1, {"channel": str(channel), "status": "failed"})

    async def _claim(self, session: AsyncSession, now: datetime) -> list[Claimed]:
        due = or_(
            and_(Delivery.status == DeliveryStatus.PENDING, Delivery.next_attempt_at <= now),
            and_(Delivery.status == DeliveryStatus.SENDING, Delivery.lease_until < now),
        )
        rows = list(
            await session.scalars(
                select(Delivery)
                .where(Delivery.channel != Channel.IN_APP, Delivery.attempts < MAX_ATTEMPTS, due)
                .order_by(Delivery.priority, Delivery.next_attempt_at)
                .limit(DISPATCH_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        )
        claimed = []
        for row in rows:
            row.status = DeliveryStatus.SENDING
            row.lease_until = now + LEASE_DURATION
            row.attempts += 1
            row.last_attempt_at = now
            claimed.append(
                Claimed(
                    id=row.id,
                    user_id=row.user_id,
                    channel=Channel(row.channel),
                    event_type=NotificationEventType(row.event_type),
                    subject_type=SubjectType(row.subject_type),
                    subject_id=row.subject_id,
                    params=dict(row.params),
                    locale=row.locale,
                    priority=row.priority,
                    attempts=row.attempts,
                    created_at=row.created_at,
                    trace_id=row.trace_id,
                    binding_id=row.binding_id,
                )
            )
        await session.flush()
        return claimed

    # --- Step 2: send-time checks, rendering and sending ----------------------------------------

    async def _prepare_isolated(
        self, session: AsyncSession, row: Claimed, now: datetime
    ) -> _Prepared:
        """One row's failure (a render bug, a database error) never stops its batchmates."""
        try:
            return await self._prepare(session, row, now)
        except Exception as exc:
            logger.exception("notification_prepare_failed", delivery_id=str(row.id))
            if isinstance(exc, SQLAlchemyError):
                await session.rollback()  # so the next rows can still read
            return _Prepared(row=row, outcome=Outcome("RETRY", "PREPARE_ERROR"))

    async def _prepare(self, session: AsyncSession, row: Claimed, now: datetime) -> _Prepared:
        def skip(code: str) -> _Prepared:
            return _Prepared(row=row, outcome=Outcome("SKIPPED", code))

        if self._settings.DEMO_MODE_ENABLED:
            return skip("DEMO_MODE")
        if row.channel not in _SENDABLE or self._plugins.channel(row.channel.value) is None:
            return skip("CHANNEL_NOT_ENABLED")
        if emergency_too_old(row.event_type, row.created_at, now):
            return skip("EMERGENCY_TOO_OLD")
        properties = PropertyService(session)
        if not await properties.is_user_active(row.user_id):
            return skip("USER_INACTIVE")
        if not is_mandatory(row.event_type):
            stored = (await stored_preferences(session, [row.user_id]))[row.user_id]
            if not is_enabled(row.event_type, row.channel, stored):
                return skip("PREFERENCE_OFF")

        title = body = ""
        if row.subject_type == SubjectType.POST:
            ref = await CommunicationService(session, properties).get_emergency_ref(row.subject_id)
            if ref is None:
                return skip("SUBJECT_NOT_FOUND")
            if row.event_type == NotificationEventType.EMERGENCY_PUBLISHED and not ref.is_active:
                return skip("POST_RESOLVED")
            title, body = ref.title, ref.body

        address = await self._address(session, properties, row)
        if isinstance(address, str):
            return skip(address)

        rendered = compose(
            row.event_type,
            row.locale,
            row.params,
            site_url=self._settings.PUBLIC_BASE_URL,
            timezone=self._settings.INSTANCE_TIMEZONE,
            title=title,
            body=body,
        )
        ttl = (
            EMERGENCY_MAX_AGE
            if row.event_type == NotificationEventType.EMERGENCY_PUBLISHED
            else OTHER_MESSAGE_TTL
        )
        message = OutgoingMessage(
            address=address,
            locale=row.locale,
            event_type=row.event_type.value,
            subject=rendered.subject,
            body=rendered.text,
            footer=rendered.footer,
            deep_link=self._settings.PUBLIC_BASE_URL,
            urgent=row.priority == 0,
            collapse_key=_collapse_key(row),
            expires_at=row.created_at + ttl,
            trace_id=row.trace_id,
        )
        return _Prepared(row=row, message=message)

    async def _address(
        self, session: AsyncSession, properties: PropertyService, row: Claimed
    ) -> dict[str, object] | str:
        """Where to send, read right before sending; a skip code when it is gone."""
        if row.channel == Channel.EMAIL:
            contact = await properties.get_delivery_contact(row.user_id)
            return {"email": contact.email} if contact else "NO_EMAIL"
        binding = await session.scalar(
            select(ChannelBinding).where(
                ChannelBinding.id == row.binding_id,
                ChannelBinding.user_id == row.user_id,
                ChannelBinding.revoked_at.is_(None),
            )
        )
        if binding is None or not binding.config:
            return "BINDING_REVOKED"  # unlinked, replaced, or blocked meanwhile
        return dict(binding.config)

    async def _send(self, item: _Prepared) -> Outcome:
        row, message = item.row, item.message
        assert message is not None
        channel = self._plugins.channel(row.channel.value)
        assert channel is not None  # checked in _prepare
        with tracer.start_as_current_span(
            "notifications.deliver",
            context=_parent_context(row.trace_id),
            attributes={
                "notification.delivery_id": str(row.id),
                "notification.event_type": row.event_type.value,
                "notification.channel": row.channel.value,
                "notification.attempt": row.attempts,
            },
        ) as span:
            try:
                async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                    result = await channel.send(message)
            except TimeoutError:
                result = DeliveryResult(ok=False, retryable=True, error_code="CHANNEL_TIMEOUT")
            except Exception:  # a plugin bug must not stop the batch; the type is enough to debug
                logger.exception("notification_channel_error", delivery_id=str(row.id))
                result = DeliveryResult(ok=False, retryable=True, error_code="CHANNEL_ERROR")
            outcome = _outcome(result)
            span.set_attribute("notification.status", outcome.status)
            if outcome.error_code:
                span.set_attribute("notification.error_code", outcome.error_code)
        return outcome

    # --- Step 3: record -------------------------------------------------------------------------

    async def _record(
        self, session: AsyncSession, row: Claimed, outcome: Outcome, now: datetime
    ) -> None:
        values: dict[str, Any] = {"lease_until": None, "error_code": outcome.error_code}
        status = outcome.status
        if status == "RETRY":
            delay = retry_delay(row.attempts)
            if delay is None:
                status = "FAILED"  # the last attempt: never back to PENDING
            else:
                values |= {"status": DeliveryStatus.PENDING, "next_attempt_at": now + delay}
        if status == "SENT":
            values |= {"status": DeliveryStatus.SENT, "sent_at": now}
        elif status in ("FAILED", "SKIPPED"):
            values["status"] = DeliveryStatus(status)
        result = await session.execute(
            update(Delivery)
            .where(
                Delivery.id == row.id,
                Delivery.status == DeliveryStatus.SENDING,
                Delivery.attempts == row.attempts,
            )
            .values(**values)
        )
        if not result.rowcount:
            # Another worker took the row over after our lease expired: its result wins.
            logger.info("notification_result_dropped", delivery_id=str(row.id))
            return
        if outcome.revoke_binding and row.binding_id is not None:
            # The provider says this chat or device is gone: stop using it (docs/SCHEMA.md 5).
            await session.execute(
                update(ChannelBinding)
                .where(ChannelBinding.id == row.binding_id, ChannelBinding.revoked_at.is_(None))
                .values(revoked_at=now, config=None)
            )
            _revoked_bindings.add(1, {"channel": row.channel.value})
        _deliveries.add(1, {"channel": row.channel.value, "status": status.lower()})
        if status in ("FAILED", "RETRY"):
            logger.warning(
                "notification_delivery_failed",
                delivery_id=str(row.id),
                channel=row.channel.value,
                error_code=outcome.error_code,
                attempt=row.attempts,
                final=status == "FAILED",
            )


def _outcome(result: DeliveryResult) -> Outcome:
    if result.ok:
        return Outcome("SENT")
    status = "RETRY" if result.retryable else "FAILED"
    return Outcome(status, result.error_code, revoke_binding=result.revoke_binding)


async def run_dispatcher_loop(dispatcher: Dispatcher) -> None:
    """Run until cancelled; a full batch is followed at once by the next one."""
    while True:
        handled = 0
        try:
            handled = await dispatcher.tick()
        except Exception:  # CancelledError is not an Exception, so cancelling still stops the loop
            logger.exception("notification_dispatch_failed")
        if handled < DISPATCH_BATCH_SIZE:
            await asyncio.sleep(DISPATCH_INTERVAL_SECONDS)
