"""Thin HTTP layer of the notifications module (docs/API.md, section 6)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.core.exceptions import AUTH_ERRORS, error_responses
from src.core.security import get_current_user
from src.modules.notifications.schemas import (
    FEED_OFFSET_MAX,
    FEED_PAGE_DEFAULT,
    FEED_PAGE_MAX,
    NotificationView,
    PreferencesResponse,
    PreferencesUpdateRequest,
    ReadAllResponse,
    ReadResponse,
    UnreadCountResponse,
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
