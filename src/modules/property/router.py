"""Thin HTTP layer of the property module."""

import uuid

from fastapi import APIRouter, Depends

from src.core.security import get_current_user
from src.modules.property.schemas import (
    CurrentUserDTO,
    MeResponse,
    PrivacyUpdateRequest,
    PrivacyUpdateResponse,
)
from src.modules.property.service import PropertyService, get_property_service

router = APIRouter(prefix="/api/v1/property", tags=["property"])


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUserDTO = Depends(get_current_user),
    service: PropertyService = Depends(get_property_service),
) -> MeResponse:
    return await service.get_me(current_user)


@router.patch("/memberships/{membership_id}/privacy", response_model=PrivacyUpdateResponse)
async def update_privacy(
    membership_id: uuid.UUID,
    body: PrivacyUpdateRequest,
    current_user: CurrentUserDTO = Depends(get_current_user),
    service: PropertyService = Depends(get_property_service),
) -> PrivacyUpdateResponse:
    return await service.update_privacy_mode(current_user, membership_id, body.privacy_display_mode)
