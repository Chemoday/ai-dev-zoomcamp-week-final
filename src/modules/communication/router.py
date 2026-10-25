"""Thin HTTP layer of the communication module (docs/API.md, section 4)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from src.core.exceptions import AUTH_ERRORS, error_responses
from src.core.security import get_current_user, require_building_roles
from src.modules.communication.policies import MODERATOR_ROLES, PUBLISHER_ROLES
from src.modules.communication.schemas import (
    COMMENTS_PAGE_DEFAULT,
    COMMENTS_PAGE_MAX,
    FEED_PAGE_MAX,
    AcknowledgeResponse,
    CommentCreateRequest,
    CommentView,
    EmergencyView,
    PostCategory,
    PostCreateRequest,
    PostDetail,
    PostType,
    PostView,
    ResolveResponse,
)
from src.modules.communication.service import CommunicationService, get_communication_service
from src.modules.property.schemas import CurrentUserDTO

router = APIRouter(
    prefix="/api/v1/communication",
    tags=["communication"],
    responses=error_responses({401: AUTH_ERRORS, 500: ["INTERNAL_SERVER_ERROR"]}),
)

publisher = require_building_roles(*sorted(PUBLISHER_ROLES))
moderator = require_building_roles(*sorted(MODERATOR_ROLES))


@router.get("/emergencies/active", response_model=list[EmergencyView])
async def active_emergencies(
    user: CurrentUserDTO = Depends(get_current_user),
    service: CommunicationService = Depends(get_communication_service),
) -> list[EmergencyView]:
    return await service.list_active_emergencies(user)


@router.post(
    "/emergencies/{post_id}/acknowledge",
    response_model=AcknowledgeResponse,
    responses=error_responses({404: ["POST_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}),
)
async def acknowledge_emergency(
    post_id: uuid.UUID,
    user: CurrentUserDTO = Depends(get_current_user),
    service: CommunicationService = Depends(get_communication_service),
) -> AcknowledgeResponse:
    return await service.acknowledge_emergency(user, post_id)


@router.post(
    "/emergencies/{post_id}/resolve",
    response_model=ResolveResponse,
    responses=error_responses(
        {403: ["FORBIDDEN"], 404: ["POST_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}
    ),
)
async def resolve_emergency(
    post_id: uuid.UUID,
    user: CurrentUserDTO = Depends(publisher),
    service: CommunicationService = Depends(get_communication_service),
) -> ResolveResponse:
    return await service.resolve_emergency(user, post_id)


@router.get(
    "/posts", response_model=list[PostView], responses=error_responses({422: ["VALIDATION_ERROR"]})
)
async def list_posts(
    post_type: PostType | None = None,
    category: PostCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=FEED_PAGE_MAX)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    user: CurrentUserDTO = Depends(get_current_user),
    service: CommunicationService = Depends(get_communication_service),
) -> list[PostView]:
    return await service.list_posts(user, post_type, category, limit, offset)


@router.post(
    "/posts",
    response_model=PostView,
    status_code=201,
    responses=error_responses(
        {
            403: ["FORBIDDEN"],
            422: ["VALIDATION_ERROR", "INVALID_TARGET", "INVALID_AUTHOR_APARTMENT"],
        }
    ),
)
async def create_post(
    body: PostCreateRequest,
    user: CurrentUserDTO = Depends(get_current_user),
    service: CommunicationService = Depends(get_communication_service),
) -> PostView:
    return await service.create_post(user, body)


@router.get(
    "/posts/{post_id}",
    response_model=PostDetail,
    responses=error_responses({404: ["POST_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}),
)
async def get_post(
    post_id: uuid.UUID,
    comments_limit: Annotated[int, Query(ge=1, le=COMMENTS_PAGE_MAX)] = COMMENTS_PAGE_DEFAULT,
    comments_offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    user: CurrentUserDTO = Depends(get_current_user),
    service: CommunicationService = Depends(get_communication_service),
) -> PostDetail:
    return await service.get_post(user, post_id, comments_limit, comments_offset)


@router.delete(
    "/posts/{post_id}",
    status_code=204,
    responses=error_responses(
        {
            403: ["FORBIDDEN"],
            404: ["POST_NOT_FOUND"],
            409: ["POST_NOT_DELETABLE"],
            422: ["VALIDATION_ERROR"],
        }
    ),
)
async def delete_post(
    post_id: uuid.UUID,
    user: CurrentUserDTO = Depends(moderator),
    service: CommunicationService = Depends(get_communication_service),
) -> Response:
    await service.delete_post(user, post_id)
    return Response(status_code=204)


@router.post(
    "/posts/{post_id}/comments",
    response_model=CommentView,
    status_code=201,
    responses=error_responses(
        {
            404: ["POST_NOT_FOUND"],
            409: ["COMMENTS_DISABLED"],
            422: ["VALIDATION_ERROR", "INVALID_AUTHOR_APARTMENT"],
        }
    ),
)
async def add_comment(
    post_id: uuid.UUID,
    body: CommentCreateRequest,
    user: CurrentUserDTO = Depends(get_current_user),
    service: CommunicationService = Depends(get_communication_service),
) -> CommentView:
    return await service.add_comment(user, post_id, body)
