"""Thin HTTP layer of the property module."""

import uuid

from fastapi import APIRouter, Depends

from src.core.exceptions import AUTH_ERRORS, error_responses
from src.core.security import get_current_user
from src.modules.property.schemas import (
    BuildingResponse,
    CurrentUserDTO,
    LocaleUpdate,
    MeResponse,
    PrivacyUpdateRequest,
    PrivacyUpdateResponse,
)
from src.modules.property.service import PropertyService, get_property_service

router = APIRouter(
    prefix="/api/v1/property",
    tags=["property"],
    responses=error_responses({401: AUTH_ERRORS, 500: ["INTERNAL_SERVER_ERROR"]}),
)


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUserDTO = Depends(get_current_user),
    service: PropertyService = Depends(get_property_service),
) -> MeResponse:
    return await service.get_me(current_user)


@router.patch(
    "/me/locale",
    response_model=LocaleUpdate,
    responses=error_responses({422: ["VALIDATION_ERROR"]}),
)
async def update_locale(
    body: LocaleUpdate,
    current_user: CurrentUserDTO = Depends(get_current_user),
    service: PropertyService = Depends(get_property_service),
) -> LocaleUpdate:
    """Language of the caller's notifications sent outside the app; null = the instance default."""
    return await service.update_locale(current_user, body.locale)


@router.get("/building", response_model=BuildingResponse)
async def get_building(
    current_user: CurrentUserDTO = Depends(get_current_user),
    service: PropertyService = Depends(get_property_service),
) -> BuildingResponse:
    return await service.get_building(current_user)


@router.patch(
    "/memberships/{membership_id}/privacy",
    response_model=PrivacyUpdateResponse,
    responses=error_responses({404: ["MEMBERSHIP_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}),
)
async def update_privacy(
    membership_id: uuid.UUID,
    body: PrivacyUpdateRequest,
    current_user: CurrentUserDTO = Depends(get_current_user),
    service: PropertyService = Depends(get_property_service),
) -> PrivacyUpdateResponse:
    return await service.update_privacy_mode(current_user, membership_id, body.privacy_display_mode)
