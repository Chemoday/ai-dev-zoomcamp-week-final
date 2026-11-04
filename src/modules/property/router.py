"""Thin HTTP layer of the property module."""

import uuid

from fastapi import APIRouter, Depends

from src.core.exceptions import AUTH_ERRORS, error_responses
from src.core.security import get_current_user, require_building_roles
from src.modules.property.policies import CARD_VIEWER_ROLES
from src.modules.property.schemas import (
    ApartmentCard,
    ApartmentListResponse,
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

card_viewer = require_building_roles(*sorted(CARD_VIEWER_ROLES))


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


@router.get(
    "/apartments",
    response_model=ApartmentListResponse,
    responses=error_responses({403: ["FORBIDDEN"]}),
)
async def list_apartment_cards(
    current_user: CurrentUserDTO = Depends(card_viewer),
    service: PropertyService = Depends(get_property_service),
) -> ApartmentListResponse:
    """The board's apartment cards (epic E8): relations and counts, never people."""
    return await service.list_apartment_cards(current_user)


@router.get(
    "/apartments/{apartment_id}",
    response_model=ApartmentCard,
    responses=error_responses(
        {403: ["FORBIDDEN"], 404: ["APARTMENT_NOT_FOUND"], 422: ["VALIDATION_ERROR"]}
    ),
)
async def get_apartment_card(
    apartment_id: uuid.UUID,
    current_user: CurrentUserDTO = Depends(card_viewer),
    service: PropertyService = Depends(get_property_service),
) -> ApartmentCard:
    """One apartment card: the list fields plus residents as relation and start date."""
    return await service.get_apartment_card(current_user, apartment_id)
