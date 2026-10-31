"""Thin HTTP layer of the notifications module (docs/API.md, section 6)."""

import uuid
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response

from src.core.config import Settings
from src.core.database import DbSession
from src.core.exceptions import AUTH_ERRORS, DomainError, error_responses
from src.core.security import get_current_user
from src.modules.notifications.linking import ChannelLinking, LinkingChannel
from src.modules.notifications.schemas import (
    FEED_OFFSET_MAX,
    FEED_PAGE_DEFAULT,
    FEED_PAGE_MAX,
    BindingView,
    Channel,
    LinkCodeResponse,
    NotificationView,
    PreferencesResponse,
    PreferencesUpdateRequest,
    ReadAllResponse,
    ReadResponse,
    UnreadCountResponse,
    VapidPublicKeyResponse,
    WebPushSubscriptionRequest,
)
from src.modules.notifications.service import NotificationsService, get_notifications_service
from src.modules.property.schemas import CurrentUserDTO

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["notifications"],
    responses=error_responses({401: AUTH_ERRORS, 500: ["INTERNAL_SERVER_ERROR"]}),
)


@router.get(
    "/feed",
    response_model=list[NotificationView],
    responses=error_responses({422: ["VALIDATION_ERROR"]}),
)
async def feed(
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=FEED_PAGE_MAX)] = FEED_PAGE_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=FEED_OFFSET_MAX)] = 0,
    user: CurrentUserDTO = Depends(get_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> list[NotificationView]:
    """The caller's in-app notifications, newest first."""
    return await service.list_feed(user, unread_only=unread_only, limit=limit, offset=offset)


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    user: CurrentUserDTO = Depends(get_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> UnreadCountResponse:
    return await service.unread_count(user)


@router.post(
    "/feed/read-all",
    response_model=ReadAllResponse,
)
async def read_all(
    user: CurrentUserDTO = Depends(get_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> ReadAllResponse:
    return await service.mark_all_read(user)


@router.post(
    "/feed/{notification_id}/read",
    response_model=ReadResponse,
    responses=error_responses({404: ["NOTIFICATION_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}),
)
async def read_one(
    notification_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> ReadResponse:
    """Idempotent: the first read time is kept."""
    return await service.mark_read(user, notification_id)


@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(
    user: CurrentUserDTO = Depends(get_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> PreferencesResponse:
    return await service.get_preferences(user)


@router.put(
    "/preferences",
    response_model=PreferencesResponse,
    responses=error_responses({422: ["VALIDATION_ERROR", "PREFERENCE_MANDATORY"]}),
)
async def update_preferences(
    body: PreferencesUpdateRequest,
    user: CurrentUserDTO = Depends(get_current_user),
    service: NotificationsService = Depends(get_notifications_service),
) -> PreferencesResponse:
    """Only the listed combinations change, all or nothing."""
    return await service.update_preferences(user, body)


# --- Linking external channels (task N4) --------------------------------------------------------


def get_channel_linking(request: Request, session: DbSession) -> ChannelLinking:
    return ChannelLinking(
        session, settings=request.app.state.settings, plugins=request.app.state.plugin_registry
    )


@router.post(
    "/telegram/link-code",
    status_code=201,
    response_model=LinkCodeResponse,
    responses=error_responses({404: ["CHANNEL_NOT_ENABLED"], 429: ["RATE_LIMITED"]}),
)
async def telegram_link_code(
    user: CurrentUserDTO = Depends(get_current_user),
    linking: ChannelLinking = Depends(get_channel_linking),
) -> LinkCodeResponse:
    """A one-time code (10 minutes) for "/start <code>" in the bot; at most 5 per hour."""
    return await linking.create_telegram_code(user)


@router.get(
    "/webpush/vapid-public-key",
    response_model=VapidPublicKeyResponse,
    responses=error_responses({404: ["CHANNEL_NOT_ENABLED"]}),
)
async def webpush_vapid_public_key(
    user: CurrentUserDTO = Depends(get_current_user),
    linking: ChannelLinking = Depends(get_channel_linking),
) -> VapidPublicKeyResponse:
    """The application server key for `pushManager.subscribe`."""
    return linking.vapid_public_key()


@router.post(
    "/webpush/subscriptions",
    status_code=201,
    response_model=BindingView,
    responses={
        200: {"model": BindingView, "description": "The endpoint was already this user's device"},
        **error_responses(
            {
                404: ["CHANNEL_NOT_ENABLED"],
                409: ["DEVICE_LIMIT_REACHED"],
                422: ["VALIDATION_ERROR", "INVALID_PUSH_ENDPOINT"],
            }
        ),
    },
)
async def webpush_subscribe(
    body: WebPushSubscriptionRequest,
    response: Response,
    user: CurrentUserDTO = Depends(get_current_user),
    linking: ChannelLinking = Depends(get_channel_linking),
) -> BindingView:
    """Register this browser for pushes (`PushSubscription.toJSON()` plus a label)."""
    view, created = await linking.subscribe_webpush(user, body)
    if not created:
        response.status_code = 200
    return view


@router.get("/bindings", response_model=list[BindingView])
async def list_bindings(
    user: CurrentUserDTO = Depends(get_current_user),
    linking: ChannelLinking = Depends(get_channel_linking),
) -> list[BindingView]:
    """The caller's linked chats and devices (addresses are never returned)."""
    return await linking.list_bindings(user)


@router.delete(
    "/bindings/{binding_id}",
    status_code=204,
    responses=error_responses({404: ["BINDING_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}),
)
async def delete_binding(
    binding_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_current_user),
    linking: ChannelLinking = Depends(get_channel_linking),
) -> Response:
    """Unlink a chat or device; queued messages for it are skipped."""
    await linking.delete_binding(user, binding_id)
    return Response(status_code=204)


# Called by Telegram, not by the frontend: no user authentication, a shared secret instead.
webhook_router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["notifications"],
    responses=error_responses({500: ["INTERNAL_SERVER_ERROR"]}),
)
WEBHOOK_BODY_LIMIT = 64 * 1024
logger = structlog.get_logger(__name__)


def _not_found() -> DomainError:
    # The same body as an unknown route: without the secret nothing tells whether the bot is on.
    return DomainError(status_code=404, error_code="NOT_FOUND", message="Not Found")


def _telegram(request: Request) -> LinkingChannel | None:
    """The enabled Telegram plugin, regardless of demo mode (the secret is checked first)."""
    plugins = request.app.state.plugin_registry
    return cast(LinkingChannel | None, plugins.channel(Channel.TELEGRAM.value))


async def _read_limited(request: Request, limit: int) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return None
    return bytes(body)


async def _reply(channel: LinkingChannel, chat_id: int, text: str) -> None:
    result = await channel.reply(chat_id, text)
    if not result.ok:
        logger.warning("telegram_reply_failed", error_code=result.error_code)


@webhook_router.post(
    "/telegram/webhook",
    responses=error_responses({404: ["NOT_FOUND"]}),
)
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    linking: ChannelLinking = Depends(get_channel_linking),
) -> dict[str, Any]:
    """Updates from the Telegram Bot API. A valid secret always gets 200 {} (no retries)."""
    channel = _telegram(request)
    if channel is None or not channel.verify(request.headers):
        raise _not_found()
    settings: Settings = request.app.state.settings
    if settings.DEMO_MODE_ENABLED:
        return {}  # demo mode links nothing
    body = await _read_limited(request, WEBHOOK_BODY_LIMIT)
    link = channel.parse_inbound(body) if body is not None else None
    if link is None:
        return {}
    reply = await linking.redeem_telegram(link)
    if reply:
        # Sent after the response, when the session has already committed the binding.
        background.add_task(_reply, channel, link.chat_id, reply)
    return {}
