"""Linking external channels: Telegram link codes and the webhook (N4), WebPush subscriptions
(N5) and the bindings API.

docs/API.md 6 ("Telegram linking", "WebPush", bindings) and docs/epics/E1_NOTIFICATIONS.md 10.7.
Codes, chat ids and push endpoints are matched only by keyed HMACs (NOTIFICATIONS_HASH_SECRET);
chat ids and endpoints never leave this module except to the plugin that sends.
"""

import uuid
from datetime import UTC, datetime
from typing import Protocol, cast

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.exceptions import DomainError
from src.modules.notifications.messages import default_catalog
from src.modules.notifications.models import ChannelBinding, LinkAttempt, LinkCode
from src.modules.notifications.policies import (
    LINK_ATTEMPTS_PER_HOUR,
    LINK_CODE_TTL,
    LINK_CODES_PER_HOUR,
    LINK_WINDOW,
    MAX_PUSH_DEVICES,
    keyed_hash,
    new_link_code,
    normalize_link_code,
    retry_after_seconds,
)
from src.modules.notifications.schemas import (
    BindingView,
    Channel,
    LinkCodeResponse,
    VapidPublicKeyResponse,
    WebPushSubscriptionRequest,
)
from src.modules.property.schemas import CurrentUserDTO
from src.modules.property.service import PropertyService
from src.plugins.registry import InboundChannel, InboundLink, PluginRegistry

TELEGRAM_LABEL = "Telegram"
logger = structlog.get_logger(__name__)


class LinkingChannel(InboundChannel, Protocol):
    """A channel plugin that links chats through a bot (common.telegram)."""

    def deep_link(self, code: str) -> str: ...


class PushChannel(Protocol):
    """A channel plugin that browsers subscribe to (common.webpush)."""

    public_key: str

    def accepts_endpoint(self, endpoint: str) -> bool: ...


def _now() -> datetime:
    return datetime.now(UTC)


def channel_not_enabled() -> DomainError:
    return DomainError(
        status_code=404, error_code="CHANNEL_NOT_ENABLED", message="This channel is not enabled."
    )


def binding_not_found() -> DomainError:
    return DomainError(status_code=404, error_code="BINDING_NOT_FOUND", message="Not linked.")


def _view(binding: ChannelBinding) -> BindingView:
    return BindingView(
        binding_id=binding.id,
        channel=Channel(binding.channel),
        label=binding.label,
        created_at=binding.created_at,
    )


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    """A transaction-scoped lock on a name, so concurrent linking of one user or one chat is
    serialized (always taken user first, then chat)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


def revoke(binding: ChannelBinding, now: datetime) -> None:
    binding.revoked_at = now
    binding.config = None  # the stored address is wiped


class ChannelLinking:
    def __init__(
        self, session: AsyncSession, *, settings: Settings, plugins: PluginRegistry
    ) -> None:
        self.session = session
        self.settings = settings
        self.plugins = plugins

    def _hash(self, value: str) -> str:
        return keyed_hash(self.settings.NOTIFICATIONS_HASH_SECRET.get_secret_value(), value)

    def telegram(self) -> LinkingChannel | None:
        """The Telegram plugin, or None in demo mode or when it is not enabled."""
        if self.settings.DEMO_MODE_ENABLED:
            return None
        return cast(LinkingChannel | None, self.plugins.channel(Channel.TELEGRAM.value))

    def webpush(self) -> PushChannel | None:
        """The WebPush plugin, or None in demo mode or when it is not enabled."""
        if self.settings.DEMO_MODE_ENABLED:
            return None
        return cast(PushChannel | None, self.plugins.channel(Channel.WEBPUSH.value))

    # --- Link codes -----------------------------------------------------------------------------

    async def create_telegram_code(self, user: CurrentUserDTO) -> LinkCodeResponse:
        channel = self.telegram()
        if channel is None:
            raise channel_not_enabled()
        now = _now()
        await _advisory_lock(self.session, f"notif:user:{user.id}")  # the count and the insert
        recent = list(
            await self.session.scalars(
                select(LinkCode.created_at)
                .where(LinkCode.user_id == user.id, LinkCode.created_at > now - LINK_WINDOW)
                .order_by(LinkCode.created_at)
            )
        )
        if len(recent) >= LINK_CODES_PER_HOUR:
            wait = retry_after_seconds(recent[0], now)
            raise DomainError(
                status_code=429,
                error_code="RATE_LIMITED",
                message="Too many link codes; try again later.",
                extra={"retry_after_seconds": wait},
                headers={"Retry-After": str(wait)},
            )
        code = new_link_code()
        expires_at = now + LINK_CODE_TTL
        self.session.add(
            LinkCode(
                user_id=user.id,
                code_hash=self._hash(code),
                created_at=now,
                expires_at=expires_at,
            )
        )
        await self.session.flush()
        return LinkCodeResponse(code=code, expires_at=expires_at, deep_link=channel.deep_link(code))

    # --- The webhook ----------------------------------------------------------------------------

    async def redeem_telegram(self, link: InboundLink) -> str | None:
        """Link the chat of a "/start <code>" message; returns the bot's reply or None (no reply).

        Group chats, chats over the attempt limit and malformed updates get no reply at all; a
        bare "/start" (no code) gets a hint and does not count as an attempt.
        """
        if link.chat_type != "private":
            return None
        catalog, default_locale = default_catalog(), self.settings.DEFAULT_LOCALE
        if not link.code:
            return catalog.render("bot.how_to_link", default_locale)
        now = _now()
        chat_hash = self._hash(str(link.chat_id))
        # Telegram delivers updates in parallel: serialize one chat's attempts, so the count and
        # the insert cannot interleave (a key of its own, outside the user -> chat lock order).
        await _advisory_lock(self.session, f"notif:telegram-attempts:{chat_hash}")
        attempts = await self.session.scalar(
            select(func.count())
            .select_from(LinkAttempt)
            .where(
                LinkAttempt.address_hash == chat_hash,
                LinkAttempt.attempted_at > now - LINK_WINDOW,
            )
        )
        if (attempts or 0) >= LINK_ATTEMPTS_PER_HOUR:
            return None
        code = normalize_link_code(link.code)
        row = None
        if code is not None:
            row = await self.session.scalar(
                select(LinkCode)
                .where(
                    LinkCode.code_hash == self._hash(code),
                    LinkCode.used_at.is_(None),
                    LinkCode.expires_at > now,
                )
                .with_for_update()
            )
        recipient = await PropertyService(self.session).get_recipient(row.user_id) if row else None
        self.session.add(
            LinkAttempt(address_hash=chat_hash, attempted_at=now, succeeded=recipient is not None)
        )
        if row is None or recipient is None:
            return catalog.render("bot.invalid_code", default_locale)
        locale = recipient.locale or default_locale

        await _advisory_lock(self.session, f"notif:user:{recipient.user_id}")
        await _advisory_lock(self.session, f"notif:telegram-chat:{chat_hash}")
        active = list(
            await self.session.scalars(
                select(ChannelBinding)
                .where(
                    ChannelBinding.channel == Channel.TELEGRAM.value,
                    ChannelBinding.revoked_at.is_(None),
                    (ChannelBinding.address_hash == chat_hash)
                    | (ChannelBinding.user_id == recipient.user_id),
                )
                .with_for_update()
            )
        )
        if any(b.address_hash == chat_hash and b.user_id == recipient.user_id for b in active):
            row.used_at = now  # the code is used up, the binding stays
            return catalog.render("bot.already_linked", locale)
        try:
            # All or nothing: on a conflict the old bindings and the code stay as they were.
            async with self.session.begin_nested():
                row.used_at = now
                for binding in active:
                    # The chat moves to this user, or this user's other chat is replaced.
                    revoke(binding, now)
                await self.session.flush()  # free the partial unique indexes first
                self.session.add(
                    ChannelBinding(
                        user_id=recipient.user_id,
                        channel=Channel.TELEGRAM.value,
                        config={"chat_id": link.chat_id},
                        address_hash=chat_hash,
                        label=TELEGRAM_LABEL,
                        created_at=now,
                    )
                )
        except IntegrityError:
            # A race the locks did not cover; nothing changed, so the same code works again.
            logger.warning("telegram_link_conflict")
            return catalog.render("bot.invalid_code", default_locale)
        return catalog.render("bot.linked", locale)

    # --- WebPush --------------------------------------------------------------------------------

    def vapid_public_key(self) -> VapidPublicKeyResponse:
        channel = self.webpush()
        if channel is None:
            raise channel_not_enabled()
        return VapidPublicKeyResponse(public_key=channel.public_key)

    async def subscribe_webpush(
        self, user: CurrentUserDTO, request: WebPushSubscriptionRequest
    ) -> tuple[BindingView, bool]:
        """Store a browser subscription as a binding; returns it and whether it is new.

        The same endpoint again for the same user updates its keys and label. An endpoint active
        for another user (a shared browser) moves to this one. Locks: the user, then the endpoint,
        so concurrent subscriptions end with one active binding per endpoint and never an error.
        """
        channel = self.webpush()
        if channel is None:
            raise channel_not_enabled()
        if not channel.accepts_endpoint(request.endpoint):
            raise DomainError(
                status_code=422,
                error_code="INVALID_PUSH_ENDPOINT",
                message="The endpoint is not a known push service.",
            )
        now = _now()
        endpoint_hash = self._hash(request.endpoint)
        config = {
            "endpoint": request.endpoint,
            "p256dh": request.keys.p256dh,
            "auth": request.keys.auth,
        }
        await _advisory_lock(self.session, f"notif:user:{user.id}")
        await _advisory_lock(self.session, f"notif:webpush-endpoint:{endpoint_hash}")
        current = await self.session.scalar(
            select(ChannelBinding)
            .where(
                ChannelBinding.channel == Channel.WEBPUSH.value,
                ChannelBinding.address_hash == endpoint_hash,
                ChannelBinding.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if current is not None and current.user_id == user.id:
            current.config = config  # the browser may have new keys for the same endpoint
            current.label = request.label
            await self.session.flush()
            return _view(current), False
        devices = await self.session.scalar(
            select(func.count())
            .select_from(ChannelBinding)
            .where(
                ChannelBinding.user_id == user.id,
                ChannelBinding.channel == Channel.WEBPUSH.value,
                ChannelBinding.revoked_at.is_(None),
            )
        )
        if (devices or 0) >= MAX_PUSH_DEVICES:
            raise DomainError(
                status_code=409,
                error_code="DEVICE_LIMIT_REACHED",
                message=f"At most {MAX_PUSH_DEVICES} devices; remove one first.",
            )
        if current is not None:
            revoke(current, now)  # a shared browser: the previous user stops receiving pushes
            await self.session.flush()  # free the partial unique index first
        binding = ChannelBinding(
            user_id=user.id,
            channel=Channel.WEBPUSH.value,
            config=config,
            address_hash=endpoint_hash,
            label=request.label,
            created_at=now,
        )
        self.session.add(binding)
        await self.session.flush()
        return _view(binding), True

    # --- Bindings -------------------------------------------------------------------------------

    async def list_bindings(self, user: CurrentUserDTO) -> list[BindingView]:
        rows = await self.session.scalars(
            select(ChannelBinding)
            .where(ChannelBinding.user_id == user.id, ChannelBinding.revoked_at.is_(None))
            .order_by(ChannelBinding.created_at, ChannelBinding.id)
        )
        return [_view(row) for row in rows]

    async def delete_binding(self, user: CurrentUserDTO, binding_id: uuid.UUID) -> None:
        row = await self.session.scalar(
            select(ChannelBinding)
            .where(
                ChannelBinding.id == binding_id,
                ChannelBinding.user_id == user.id,
                ChannelBinding.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if row is None:  # someone else's, unknown or already revoked: the same answer
            raise binding_not_found()
        revoke(row, _now())
        await self.session.flush()
