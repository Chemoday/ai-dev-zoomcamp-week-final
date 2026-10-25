"""Public enums and DTOs of the property module (API payloads and inter-module contracts).

None of these DTOs carries a raw full name, email or phone: identity leaves the module only as
an AuthorBadgeDTO, already masked according to the user's privacy display mode.
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class BuildingRole(StrEnum):
    CHAIRMAN = "CHAIRMAN"
    BOARD = "BOARD"
    ACCOUNTANT = "ACCOUNTANT"
    USER = "USER"


class RelationType(StrEnum):
    OWNER = "OWNER"
    TENANT = "TENANT"
    RESIDENT = "RESIDENT"


class DisplayMode(StrEnum):
    APARTMENT_ONLY = "APARTMENT_ONLY"
    INITIALS = "INITIALS"
    FULL_NAME = "FULL_NAME"


class RoleBadge(StrEnum):
    OWNER = "OWNER"
    RESIDENT = "RESIDENT"
    BOARD = "BOARD"
    ACCOUNTANT = "ACCOUNTANT"


class AuthorBadgeDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    display_label: str
    role_badge: RoleBadge


class ApartmentDTO(BaseModel):
    """An apartment as other modules see it (no residents, no names)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    number: str
    entrance: int
    floor: int


class ApartmentMembershipDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: uuid.UUID
    apartment_id: uuid.UUID
    apartment_number: str
    entrance: int
    floor: int
    relation_type: RelationType
    can_submit_readings: bool
    can_view_bills: bool
    can_vote: bool
    privacy_display_mode: DisplayMode


class CurrentUserDTO(BaseModel):
    """The authenticated user as seen by every module (returned by core.security)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    building_id: uuid.UUID
    building_role: BuildingRole
    privacy_display_mode: DisplayMode
    memberships: tuple[ApartmentMembershipDTO, ...] = ()

    def membership_for(self, apartment_id: uuid.UUID) -> ApartmentMembershipDTO | None:
        return next((m for m in self.memberships if m.apartment_id == apartment_id), None)

    @property
    def apartment_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(m.apartment_id for m in self.memberships)


class MembershipView(ApartmentMembershipDTO):
    preview_badge: AuthorBadgeDTO


class MeResponse(BaseModel):
    user_id: uuid.UUID
    building_id: uuid.UUID
    building_role: BuildingRole
    privacy_display_mode: DisplayMode
    memberships: list[MembershipView]


class PrivacyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy_display_mode: DisplayMode


class PrivacyUpdateResponse(BaseModel):
    membership_id: uuid.UUID
    privacy_display_mode: DisplayMode
    preview_badge: AuthorBadgeDTO
