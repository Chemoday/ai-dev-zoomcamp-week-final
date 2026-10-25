"""Demo mode helpers for the role switcher (docs/API.md, section 5).

Only available while DEMO_MODE_ENABLED is true; otherwise the route answers 404 like an unknown
one. The list carries labels and roles of the fixed demo users, never names, emails or phones.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.core.config import Settings, get_settings
from src.core.exceptions import DomainError, error_responses
from src.core.seed import DEMO_USERS
from src.modules.property.schemas import BuildingRole, RelationType
from src.modules.property.service import PropertyService, get_property_service

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


class DemoUserView(BaseModel):
    user_id: uuid.UUID  # send it as X-Demo-User-Id
    label: str  # e.g. "Owner Apt 14"
    building_role: BuildingRole
    apartment_number: str | None
    relation_type: RelationType | None


@router.get(
    "/users",
    response_model=list[DemoUserView],
    responses=error_responses({404: ["NOT_FOUND"], 500: ["INTERNAL_SERVER_ERROR"]}),
)
async def demo_users(
    settings: Settings = Depends(get_settings),
    property_service: PropertyService = Depends(get_property_service),
) -> list[DemoUserView]:
    """The seeded demo users that exist and are active on this instance, in switcher order.

    No authentication: the role switcher needs the list before anyone is "logged in".
    """
    if not settings.DEMO_MODE_ENABLED:
        raise DomainError(status_code=404, error_code="NOT_FOUND", message="Not Found")
    return [
        DemoUserView(
            user_id=demo.user_id,
            label=demo.label,
            building_role=demo.building_role,
            apartment_number=demo.apartment_number,
            relation_type=demo.relation_type,
        )
        for demo in DEMO_USERS
        if await property_service.get_current_user(demo.user_id) is not None
    ]
