"""Public facade of the notifications module: the in-app feed, preferences, and the subscriber
that turns domain events into notifications (docs/API.md section 6, docs/ARCHITECTURE.md 3).

This module only consumes: it subscribes to events and calls PropertyService; no other module
imports it. In this task only the built-in `in_app` channel is delivered; external channels
(dispatcher, plugins) follow in N3-N5.
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
from src.core.event_bus import EventBus
from src.core.exceptions import DomainError
from src.modules.communication.events import EmergencyAlertPublished, EmergencyAlertResolved
from src.modules.meters.events import ReadingPeriodStatusChanged
from src.modules.meters.schemas import PeriodStatus
from src.modules.notifications.models import ChannelBinding, Delivery, Preference
from src.modules.notifications.policies import (
    CHANNEL_PLUGINS,
    DEFAULT_PREFERENCES,
    channel_unavailable_reason,
    emergency_key,
    emergency_resolved_key,
    is_enabled,
    is_mandatory,
    period_review_key,
    priority_for,
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
)
from src.modules.property.schemas import BuildingRole, CurrentUserDTO, Locale, RecipientDTO
from src.modules.property.service import PropertyService
from src.plugins.registry import PluginRegistry

type SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
type StoredPreferences = dict[tuple[NotificationEventType, Channel], bool]

_EMERGENCY_ROLES = (BuildingRole.CHAIRMAN, BuildingRole.BOARD)
_PERIOD_REVIEW_ROLES = (BuildingRole.CHAIRMAN, BuildingRole.ACCOUNTANT)


def _now() -> datetime:
    return datetime.now(UTC)


def _notification_not_found() -> DomainError:
    # The same answer for "unknown" and "someone else's", so ids cannot be probed.
    return DomainError(
        status_code=404, error_code="NOTIFICATION_NOT_FOUND", message="Notification not found."
    )


async def _stored_preferences(
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
        stored = (await _stored_preferences(self.session, [user.id]))[user.id]
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
            matches = (
                Preference.user_id == user.id,
                Preference.event_type == change.event_type,
                Preference.channel == change.channel,
            )
            default = DEFAULT_PREFERENCES[(change.event_type, change.channel)]
            if is_mandatory(change.event_type) or change.enabled == default:
                # Only deviations from the default are stored.
                await self.session.execute(delete(Preference).where(*matches))
                continue
            statement = pg_insert(Preference).values(
                id=uuid.uuid4(),
                user_id=user.id,
                event_type=change.event_type,
                channel=change.channel,
                enabled=change.enabled,
                updated_at=_now(),
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_notif_preferences_user_event_channel",
                    set_={"enabled": statement.excluded.enabled, "updated_at": _now()},
                )
            )
        await self.session.flush()
        return await self.get_preferences(user)


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


class NotificationsSubscriber:
    """Turns domain events into in-app notifications.

    Handlers only insert rows (never send), each in its own short transaction from the injected
    session factory, and are idempotent through `uq_notif_deliveries_dedup`: the durable outbox
    may deliver an event more than once (docs/BACKEND_CONVENTIONS.md 2.B). Events processed late
    still reach people in the app; external channels will respect `external_channels_allowed`.
    """

    def __init__(self, session_factory: Callable[[], SessionFactory], default_locale: str) -> None:
        # A provider, read on every event, so tests can swap the factory after create_app.
        self._session_factory = session_factory
        self._default_locale = Locale(default_locale)

    def subscribe(self, bus: EventBus) -> None:
        bus.subscribe(EmergencyAlertPublished, self.on_emergency_published)
        bus.subscribe(EmergencyAlertResolved, self.on_emergency_resolved)
        bus.subscribe(ReadingPeriodStatusChanged, self.on_period_status_changed)

    async def on_emergency_published(self, event: EmergencyAlertPublished) -> None:
        async with self._session_factory()() as session:
            properties = PropertyService(session)
            targeted = await properties.resolve_recipients(
                event.building_id,
                event.target_scope,
                event.target_entrance,
                event.target_apartment_ids,
            )
            board = await properties.users_with_roles(event.building_id, _EMERGENCY_ROLES)
            recipients = {r.user_id: r for r in (*targeted, *board)}
            recipients.pop(event.published_by_user_id, None)  # never the author
            await self._insert_in_app(
                session,
                recipients.values(),
                event_type=NotificationEventType.EMERGENCY_PUBLISHED,
                dedup_key=emergency_key(event.post_id),
                subject=(SubjectType.POST, event.post_id),
                params={
                    "category": event.category.value,
                    "target_scope": event.target_scope.value,
                    "target_entrance": event.target_entrance,
                },
                respect_preferences=False,  # mandatory
            )
            await session.commit()

    async def on_emergency_resolved(self, event: EmergencyAlertResolved) -> None:
        async with self._session_factory()() as session:
            rows = await session.execute(
                select(Delivery.user_id, Delivery.locale, Delivery.params).where(
                    Delivery.dedup_key == emergency_key(event.post_id),
                    Delivery.channel == Channel.IN_APP,
                    Delivery.status == DeliveryStatus.SENT,
                    Delivery.user_id != event.resolved_by_user_id,
                )
            )
            published = rows.all()
            if not published:
                return  # nobody was told about the alert, so nobody is told it is over
            params = dict(published[0].params)
            await self._insert_in_app(
                session,
                [RecipientDTO(user_id=user_id, locale=locale) for user_id, locale, _ in published],
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
        async with self._session_factory()() as session:
            staff = await PropertyService(session).users_with_roles(
                event.building_id, _PERIOD_REVIEW_ROLES
            )
            recipients = [r for r in staff if r.user_id != event.changed_by_user_id]
            await self._insert_in_app(
                session,
                recipients,
                event_type=NotificationEventType.PERIOD_REVIEW,
                dedup_key=period_review_key(event.period_id, event.event_id),
                subject=(SubjectType.PERIOD, event.period_id),
                params={"period_code": event.period_code},
                respect_preferences=True,
            )
            await session.commit()

    async def _insert_in_app(
        self,
        session: AsyncSession,
        recipients: Iterable[RecipientDTO],
        *,
        event_type: NotificationEventType,
        dedup_key: str,
        subject: tuple[SubjectType, uuid.UUID],
        params: dict[str, Any],
        respect_preferences: bool,
    ) -> None:
        recipients = list(recipients)
        if respect_preferences:
            stored = await _stored_preferences(session, [r.user_id for r in recipients])
            recipients = [
                r for r in recipients if is_enabled(event_type, Channel.IN_APP, stored[r.user_id])
            ]
        if not recipients:
            return
        now, trace_id = _now(), _current_trace_id()
        rows: Sequence[dict[str, Any]] = [
            {
                "id": uuid.uuid4(),
                "user_id": recipient.user_id,
                "channel": Channel.IN_APP,
                "binding_id": None,
                "event_type": event_type,
                "dedup_key": dedup_key,
                "subject_type": subject[0],
                "subject_id": subject[1],
                "params": params,
                "locale": recipient.locale or self._default_locale,
                "priority": priority_for(event_type),
                # In-app notifications are "sent" by being stored: the dispatcher never sees them.
                "status": DeliveryStatus.SENT,
                "attempts": 0,
                "next_attempt_at": now,
                "trace_id": trace_id,
                "created_at": now,
                "sent_at": now,
            }
            for recipient in recipients
        ]
        await session.execute(
            pg_insert(Delivery)
            .values(list(rows))
            .on_conflict_do_nothing(constraint="uq_notif_deliveries_dedup")
        )
