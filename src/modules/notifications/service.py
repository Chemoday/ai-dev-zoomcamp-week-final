"""Public facade of the notifications module: the in-app feed, preferences, and the subscriber
that turns domain events into notifications (docs/API.md section 6, docs/ARCHITECTURE.md 3).

This module only consumes: it subscribes to events and calls PropertyService; no other module
imports it. The subscriber stores rows; `in_app` rows are delivered by being stored, external ones
(e-mail since N3) are sent by the dispatcher (dispatcher.py).
"""

import uuid
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from opentelemetry import trace
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.database import DbSession
from src.core.domain_events import SessionFactory
from src.core.event_bus import EventBus
from src.core.exceptions import DomainError
from src.modules.auth.events import UserAccountClosed
from src.modules.communication.events import (
    ApartmentMessagePosted,
    EmergencyAlertPublished,
    EmergencyAlertResolved,
)
from src.modules.communication.service import CommunicationService
from src.modules.meters.events import ReadingPeriodStatusChanged
from src.modules.meters.schemas import PeriodStatus
from src.modules.notifications.models import ChannelBinding, Delivery, LinkCode, Preference
from src.modules.notifications.policies import (
    CHANNEL_PLUGINS,
    DEFAULT_PREFERENCES,
    apartment_message_key,
    channel_unavailable_reason,
    emergency_key,
    emergency_resolved_key,
    external_channels_allowed,
    is_enabled,
    is_mandatory,
    period_review_key,
    priority_for,
    split_message_recipients,
    unsubscribe_delivery_id,
)
from src.modules.notifications.schemas import (
    Channel,
    ChannelAvailability,
    DeliveryStatus,
    NotificationEventType,
    NotificationView,
    PreferencesResponse,
    PreferencesUpdateRequest,
    PreferenceView,
    ReadAllResponse,
    ReadResponse,
    SubjectType,
    UnreadCountResponse,
    UnsubscribeResponse,
)
from src.modules.property.schemas import (
    BuildingRole,
    CurrentUserDTO,
    Locale,
    RecipientDTO,
    TargetScope,
)
from src.modules.property.service import PropertyService
from src.plugins.registry import PluginRegistry

type StoredPreferences = dict[tuple[NotificationEventType, Channel], bool]

_EMERGENCY_ROLES = (BuildingRole.CHAIRMAN, BuildingRole.BOARD)
_PERIOD_REVIEW_ROLES = (BuildingRole.CHAIRMAN, BuildingRole.ACCOUNTANT)
# Candidates for APARTMENT_MESSAGE_TO_BOARD; communication's visibility rule has the last word.
_THREAD_BOARD_ROLES = (BuildingRole.CHAIRMAN, BuildingRole.BOARD)


def _now() -> datetime:
    return datetime.now(UTC)


def _notification_not_found() -> DomainError:
    # The same answer for "unknown" and "someone else's", so ids cannot be probed.
    return DomainError(
        status_code=404, error_code="NOTIFICATION_NOT_FOUND", message="Notification not found."
    )


async def stored_preferences(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, StoredPreferences]:
    ids = list(user_ids)
    result: dict[uuid.UUID, StoredPreferences] = {user_id: {} for user_id in ids}
    if not ids:
        return result
    rows = await session.execute(
        select(
            Preference.user_id, Preference.event_type, Preference.channel, Preference.enabled
        ).where(Preference.user_id.in_(ids))
    )
    for user_id, event_type, channel, enabled in rows.all():
        result[user_id][(event_type, channel)] = enabled
    return result


# --- The feed and preferences API ----------------------------------------------------------------


class NotificationsService:
    def __init__(
        self,
        session: AsyncSession,
        property_service: PropertyService,
        *,
        demo_mode: bool,
        plugins: PluginRegistry,
    ) -> None:
        self.session = session
        self.property_service = property_service
        self.demo_mode = demo_mode
        self.plugins = plugins

    async def list_feed(
        self, user: CurrentUserDTO, *, unread_only: bool, limit: int, offset: int
    ) -> list[NotificationView]:
        query = select(Delivery).where(
            Delivery.user_id == user.id, Delivery.channel == Channel.IN_APP
        )
        if unread_only:
            query = query.where(Delivery.read_at.is_(None))
        rows = await self.session.scalars(
            query.order_by(Delivery.created_at.desc(), Delivery.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            NotificationView(
                id=row.id,
                event_type=row.event_type,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                params=row.params,
                created_at=row.created_at,
                read_at=row.read_at,
            )
            for row in rows
        ]

    async def unread_count(self, user: CurrentUserDTO) -> UnreadCountResponse:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(
                Delivery.user_id == user.id,
                Delivery.channel == Channel.IN_APP,
                Delivery.read_at.is_(None),
            )
        )
        return UnreadCountResponse(unread=count or 0)

    async def mark_read(self, user: CurrentUserDTO, notification_id: uuid.UUID) -> ReadResponse:
        row = await self.session.scalar(
            select(Delivery)
            .where(
                Delivery.id == notification_id,
                Delivery.user_id == user.id,
                Delivery.channel == Channel.IN_APP,
            )
            .with_for_update()
        )
        if row is None:
            raise _notification_not_found()
        if row.read_at is None:  # idempotent: the first read time is kept
            row.read_at = _now()
            await self.session.flush()
        return ReadResponse(id=row.id, read_at=row.read_at)

    async def mark_all_read(self, user: CurrentUserDTO) -> ReadAllResponse:
        result = await self.session.execute(
            update(Delivery)
            .where(
                Delivery.user_id == user.id,
                Delivery.channel == Channel.IN_APP,
                Delivery.read_at.is_(None),
            )
            .values(read_at=_now())
        )
        return ReadAllResponse(updated=result.rowcount or 0)

    async def get_preferences(self, user: CurrentUserDTO) -> PreferencesResponse:
        stored = (await stored_preferences(self.session, [user.id]))[user.id]
        bound_channels = set(
            await self.session.scalars(
                select(ChannelBinding.channel).where(
                    ChannelBinding.user_id == user.id, ChannelBinding.revoked_at.is_(None)
                )
            )
        )
        has_email = await self.property_service.has_delivery_email(user.id)
        channels = []
        for channel in Channel:
            reason = channel_unavailable_reason(
                channel,
                demo_mode=self.demo_mode,
                plugin_enabled=channel in CHANNEL_PLUGINS
                and self.plugins.is_enabled(CHANNEL_PLUGINS[channel]),
                has_email=has_email,
                has_binding=channel.value in bound_channels,
            )
            channels.append(
                ChannelAvailability(channel=channel, available=reason is None, reason=reason)
            )
        return PreferencesResponse(
            channels=channels,
            preferences=[
                PreferenceView(
                    event_type=event_type,
                    channel=channel,
                    enabled=is_enabled(event_type, channel, stored),
                    mandatory=is_mandatory(event_type),
                )
                for event_type in NotificationEventType
                for channel in Channel
            ],
        )

    async def update_preferences(
        self, user: CurrentUserDTO, request: PreferencesUpdateRequest
    ) -> PreferencesResponse:
        """All or nothing: a mandatory combination switched off rejects the whole request."""
        for change in request.preferences:
            if is_mandatory(change.event_type) and not change.enabled:
                raise DomainError(
                    status_code=422,
                    error_code="PREFERENCE_MANDATORY",
                    message="Emergency alerts cannot be switched off.",
                    extra={"event_type": change.event_type.value, "channel": change.channel.value},
                )
        for change in request.preferences:
            await store_preference(
                self.session, user.id, change.event_type, change.channel, change.enabled
            )
        await self.session.flush()
        return await self.get_preferences(user)


async def store_preference(
    session: AsyncSession,
    user_id: uuid.UUID,
    event_type: NotificationEventType,
    channel: Channel,
    enabled: bool,
) -> None:
    """Store one preference; only deviations from the default are kept (mandatory ones never)."""
    matches = (
        Preference.user_id == user_id,
        Preference.event_type == event_type,
        Preference.channel == channel,
    )
    if is_mandatory(event_type) or enabled == DEFAULT_PREFERENCES[(event_type, channel)]:
        await session.execute(delete(Preference).where(*matches))
        return
    statement = pg_insert(Preference).values(
        id=uuid.uuid4(),
        user_id=user_id,
        event_type=event_type,
        channel=channel,
        enabled=enabled,
        updated_at=_now(),
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_notif_preferences_user_event_channel",
            set_={"enabled": statement.excluded.enabled, "updated_at": _now()},
        )
    )


def invalid_unsubscribe_link() -> DomainError:
    # One answer for every unusable link: the cause is never told.
    return DomainError(
        status_code=400, error_code="INVALID_LINK", message="This link cannot be used."
    )


async def unsubscribe(session: AsyncSession, settings: Settings, token: str) -> UnsubscribeResponse:
    """The one-click unsubscribe of an e-mail (N8): that event type off on e-mail. No sign-in;
    the token is the delivery id and its HMAC."""
    secret = settings.NOTIFICATIONS_HASH_SECRET.get_secret_value()
    delivery_id = unsubscribe_delivery_id(secret, token) if secret else None
    row = None
    if delivery_id is not None:
        row = (
            await session.execute(
                select(Delivery.user_id, Delivery.event_type, Delivery.channel).where(
                    Delivery.id == delivery_id
                )
            )
        ).first()
    if (
        row is None
        or row.channel != Channel.EMAIL
        or is_mandatory(row.event_type)
        or not await PropertyService(session).is_user_active(row.user_id)
    ):
        raise invalid_unsubscribe_link()
    await store_preference(session, row.user_id, row.event_type, Channel.EMAIL, False)
    await session.flush()
    trace.get_current_span().set_attribute("notification.event_type", row.event_type.value)
    return UnsubscribeResponse(event_type=row.event_type, channel=Channel.EMAIL)


def get_notifications_service(request: Request, session: DbSession) -> NotificationsService:
    settings: Settings = request.app.state.settings
    return NotificationsService(
        session,
        PropertyService(session),
        demo_mode=settings.DEMO_MODE_ENABLED,
        plugins=request.app.state.plugin_registry,
    )


# --- From domain events to notifications ---------------------------------------------------------


def _current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


# One delivery target: a recipient, and the binding (chat, device) for channels that have one.
type Target = tuple[RecipientDTO, uuid.UUID | None]

# External channels that reach a user through a binding row (e-mail uses core_users.email).
_BINDING_CHANNELS = (Channel.TELEGRAM, Channel.WEBPUSH)


class NotificationsSubscriber:
    """Turns domain events into notification rows: `in_app` (stored as SENT) and, where the user is
    reachable, external ones (stored as PENDING for the dispatcher): e-mail once per user, Telegram
    and WebPush once per active binding.

    Handlers only insert rows (never send), each in its own short transaction from the injected
    session factory, and are idempotent through `uq_notif_deliveries_dedup`: the durable outbox
    may deliver an event more than once (docs/BACKEND_CONVENTIONS.md 2.B). An emergency processed
    more than an hour late reaches people in the app only (`external_channels_allowed`).
    """

    def __init__(
        self,
        session_factory_provider: Callable[[], SessionFactory],
        default_locale: str,
        *,
        demo_mode: bool = True,
        plugins: PluginRegistry | None = None,
    ) -> None:
        # Asked on every event, so tests can swap the factory after create_app.
        self._session_factory_provider = session_factory_provider
        self._default_locale = Locale(default_locale)
        self._demo_mode = demo_mode
        self._plugins = plugins

    def _external_channels(self) -> list[Channel]:
        # Demo mode (anyone can act as anyone) never sends outside the app (owner decision).
        if self._demo_mode or self._plugins is None:
            return []
        return [c for c, plugin in CHANNEL_PLUGINS.items() if self._plugins.is_enabled(plugin)]

    async def _external_targets(
        self, session: AsyncSession, recipients: Iterable[RecipientDTO]
    ) -> dict[Channel, list[Target]]:
        """Where each recipient can be reached outside the app, per enabled channel."""
        recipients = list(recipients)
        by_user = {r.user_id: r for r in recipients}
        targets: dict[Channel, list[Target]] = {}
        channels = self._external_channels()
        if Channel.EMAIL in channels:
            reachable = await PropertyService(session).with_delivery_email(by_user)
            targets[Channel.EMAIL] = [(r, None) for r in recipients if r.user_id in reachable]
        bound = [c for c in channels if c in _BINDING_CHANNELS]
        if bound and by_user:
            rows = await session.execute(
                select(ChannelBinding.channel, ChannelBinding.user_id, ChannelBinding.id)
                .where(
                    ChannelBinding.user_id.in_(list(by_user)),
                    ChannelBinding.channel.in_([c.value for c in bound]),
                    ChannelBinding.revoked_at.is_(None),
                )
                .order_by(ChannelBinding.id)
            )
            for channel, user_id, binding_id in rows.all():
                targets.setdefault(Channel(channel), []).append((by_user[user_id], binding_id))
        return targets

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self._session_factory_provider()()

    def subscribe(self, bus: EventBus) -> None:
        bus.subscribe(EmergencyAlertPublished, self.on_emergency_published)
        bus.subscribe(EmergencyAlertResolved, self.on_emergency_resolved)
        bus.subscribe(ReadingPeriodStatusChanged, self.on_period_status_changed)
        bus.subscribe(ApartmentMessagePosted, self.on_apartment_message)
        bus.subscribe(UserAccountClosed, self.on_account_closed)

    async def on_account_closed(self, event: UserAccountClosed) -> None:
        """Drop everything kept for a closed account (idempotent: a retry deletes nothing)."""
        async with self._session() as session:
            await delete_user_rows(session, [event.user_id])
            await session.commit()

    async def on_emergency_published(self, event: EmergencyAlertPublished) -> None:
        async with self._session() as session:
            properties = PropertyService(session)
            # Events are unordered (a failed handler is retried by the relay later), so check
            # the alert itself: a resolved one notifies nobody. The share lock makes a
            # concurrent resolve wait until these rows are stored, so its "resolved" handler
            # always finds them.
            ref = await CommunicationService(session, properties).get_emergency_ref(
                event.post_id, lock_for_share=True
            )
            if ref is None or not ref.is_active:
                return
            # Targeting comes from the stored alert, the one source of truth.
            targeted = await properties.resolve_recipients(
                ref.building_id, ref.target_scope, ref.target_entrance, ref.target_apartment_ids
            )
            board = await properties.users_with_roles(ref.building_id, _EMERGENCY_ROLES)
            recipients = {r.user_id: r for r in (*targeted, *board)}
            recipients.pop(ref.author_user_id, None)  # never the author
            rows = {
                "event_type": NotificationEventType.EMERGENCY_PUBLISHED,
                "dedup_key": emergency_key(event.post_id),
                "subject": (SubjectType.POST, event.post_id),
                "params": {
                    "category": ref.category.value,
                    "target_scope": ref.target_scope.value,
                    "target_entrance": ref.target_entrance,
                },
                "respect_preferences": False,  # mandatory
            }
            in_app = [(r, None) for r in recipients.values()]
            await self._insert(session, in_app, channel=Channel.IN_APP, **rows)
            if external_channels_allowed(event.occurred_at, _now()):
                external = await self._external_targets(session, recipients.values())
                for channel, targets in external.items():
                    await self._insert(session, targets, channel=channel, **rows)
            await session.commit()

    async def on_emergency_resolved(self, event: EmergencyAlertResolved) -> None:
        async with self._session() as session:
            channels = [Channel.IN_APP, *self._external_channels()]
            rows = await session.execute(
                select(
                    Delivery.user_id,
                    Delivery.locale,
                    Delivery.params,
                    Delivery.channel,
                    Delivery.binding_id,
                ).where(
                    Delivery.dedup_key == emergency_key(event.post_id),
                    Delivery.channel.in_(channels),
                    Delivery.status == DeliveryStatus.SENT,
                    Delivery.user_id != event.resolved_by_user_id,
                )
            )
            published = rows.all()
            if not published:
                return  # nobody was told about the alert, so nobody is told it is over
            params = dict(published[0].params)
            # One "resolved" per alert that actually reached the person (same channel and device).
            for channel in channels:
                await self._insert(
                    session,
                    [
                        (RecipientDTO(user_id=user_id, locale=locale), binding_id)
                        for user_id, locale, _, sent_on, binding_id in published
                        if sent_on == channel
                    ],
                    channel=channel,
                    event_type=NotificationEventType.EMERGENCY_RESOLVED,
                    dedup_key=emergency_resolved_key(event.post_id),
                    subject=(SubjectType.POST, event.post_id),
                    params=params,
                    respect_preferences=True,
                )
            await session.commit()

    async def on_period_status_changed(self, event: ReadingPeriodStatusChanged) -> None:
        if event.new_status != PeriodStatus.REVIEW_BY_BOARD:
            return
        async with self._session() as session:
            staff = await PropertyService(session).users_with_roles(
                event.building_id, _PERIOD_REVIEW_ROLES
            )
            recipients = [r for r in staff if r.user_id != event.changed_by_user_id]
            await self.notify(
                session,
                recipients,
                event_type=NotificationEventType.PERIOD_REVIEW,
                dedup_key=period_review_key(event.period_id, event.event_id),
                subject=(SubjectType.PERIOD, event.period_id),
                params={"period_code": event.period_code},
            )
            await session.commit()

    async def on_apartment_message(self, event: ApartmentMessagePosted) -> None:
        """A new thread or reply: the apartment's members who see it, and the board quietly.

        Candidates come from property, and communication's visibility rule decides who of them
        sees the thread (the "since moving in" rule included); the dispatcher asks again before
        sending. A message notifies on every channel the person wants: there is no "once until
        read", the threads are low volume (owner decision, E8).
        """
        async with self._session() as session:
            properties = PropertyService(session)
            communication = CommunicationService(session, properties)
            thread = await communication.get_thread_ref(event.post_id)
            if thread is None:
                return
            apartment = await properties.apartment_ref(thread.apartment_id)
            if apartment is None:
                return
            members = await properties.resolve_recipients(
                thread.building_id, TargetScope.APARTMENT_LIST, None, [thread.apartment_id]
            )
            board = await properties.users_with_roles(thread.building_id, _THREAD_BOARD_ROLES)
            candidates = {r.user_id: r for r in (*members, *board)}
            # Both groups pass the one visibility rule: the roles only narrow the candidates.
            readers = [
                r.user_id
                for r in members
                if await self._can_read(properties, communication, r.user_id, thread.post_id)
            ]
            board_readers = [
                r.user_id
                for r in board
                if await self._can_read(properties, communication, r.user_id, thread.post_id)
            ]
            residents, board_only = split_message_recipients(
                event.author_user_id, event.from_board, readers, board_readers
            )
            subject = (SubjectType.POST, thread.post_id)
            params = {
                "apartment_id": str(thread.apartment_id),
                "apartment_number": apartment.number,
                "from_board": event.from_board,
            }
            dedup_key = apartment_message_key(thread.post_id, event.comment_id)
            for event_type, user_ids in (
                (NotificationEventType.APARTMENT_MESSAGE, residents),
                (NotificationEventType.APARTMENT_MESSAGE_TO_BOARD, board_only),
            ):
                await self.notify(
                    session,
                    [candidates[user_id] for user_id in user_ids],
                    event_type=event_type,
                    dedup_key=dedup_key,
                    subject=subject,
                    params=params,
                )
            await session.commit()

    @staticmethod
    async def _can_read(
        properties: PropertyService,
        communication: CommunicationService,
        user_id: uuid.UUID,
        post_id: uuid.UUID,
    ) -> bool:
        user = await properties.get_current_user(user_id)
        return user is not None and await communication.can_read_post(user, post_id)

    async def notify(
        self,
        session: AsyncSession,
        recipients: Iterable[RecipientDTO],
        *,
        event_type: NotificationEventType,
        dedup_key: str,
        subject: tuple[SubjectType, uuid.UUID],
        params: dict[str, Any],
        now: datetime | None = None,
    ) -> None:
        """Insert a non-mandatory notification for these recipients on every channel they want
        and can be reached on (in the caller's transaction; the caller commits). Also used by
        the scheduler for reading reminders."""
        recipients = list(recipients)
        rows = {
            "event_type": event_type,
            "dedup_key": dedup_key,
            "subject": subject,
            "params": params,
            "respect_preferences": True,
            "now": now,
        }
        await self._insert(session, [(r, None) for r in recipients], channel=Channel.IN_APP, **rows)
        for channel, targets in (await self._external_targets(session, recipients)).items():
            await self._insert(session, targets, channel=channel, **rows)

    async def _insert(
        self,
        session: AsyncSession,
        targets: Iterable[Target],
        *,
        channel: Channel,
        event_type: NotificationEventType,
        dedup_key: str,
        subject: tuple[SubjectType, uuid.UUID],
        params: dict[str, Any],
        respect_preferences: bool,
        now: datetime | None = None,
    ) -> None:
        targets = list(targets)
        if respect_preferences:
            stored = await stored_preferences(session, {r.user_id for r, _ in targets})
            targets = [
                (r, binding_id)
                for r, binding_id in targets
                if is_enabled(event_type, channel, stored[r.user_id])
            ]
        if not targets:
            return
        # The scheduler passes its tick time, so one tick stamps its rows alike (and tests can
        # run at any date).
        now, trace_id = now or _now(), _current_trace_id()
        # In-app notifications are "sent" by being stored; external ones wait for the dispatcher.
        in_app = channel == Channel.IN_APP
        rows: Sequence[dict[str, Any]] = [
            {
                "id": uuid.uuid4(),
                "user_id": recipient.user_id,
                "channel": channel,
                "binding_id": binding_id,  # set exactly for telegram and webpush
                "event_type": event_type,
                "dedup_key": dedup_key,
                "subject_type": subject[0],
                "subject_id": subject[1],
                "params": params,
                "locale": recipient.locale or self._default_locale,
                "priority": priority_for(event_type),
                "status": DeliveryStatus.SENT if in_app else DeliveryStatus.PENDING,
                "attempts": 0,
                "next_attempt_at": now,
                "trace_id": trace_id,
                "created_at": now,
                "sent_at": now if in_app else None,
            }
            for recipient, binding_id in targets
        ]
        # executemany style, so SQLAlchemy batches large buildings instead of building one
        # statement with more bind parameters than PostgreSQL accepts.
        await session.execute(
            pg_insert(Delivery).on_conflict_do_nothing(constraint="uq_notif_deliveries_dedup"),
            list(rows),
        )


async def delete_user_rows(session: AsyncSession, user_ids: Iterable[uuid.UUID]) -> int:
    """Delete the people's feed and deliveries, chat and device bindings, preferences and link
    codes (a closed account, docs/ARCHITECTURE.md 4.1). Returns the rows deleted."""
    ids = list(user_ids)
    if not ids:
        return 0
    deleted = 0
    # Deliveries first: they point at bindings.
    for model in (Delivery, ChannelBinding, Preference, LinkCode):
        result = await session.execute(
            delete(model).where(model.user_id.in_(ids)).execution_options(synchronize_session=False)
        )
        deleted += result.rowcount or 0
    return deleted
