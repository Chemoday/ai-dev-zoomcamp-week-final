"""Public enums and DTOs of the auth module (docs/API.md section 7)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from src.core.validation import canonical_email, clean_text, optional_safe_text, safe_text
from src.modules.property.schemas import BuildingRole, DisplayMode, Locale, RelationType

MAX_EMAIL_LENGTH = 254


def _email_address(value: str) -> str:
    """The canonical form of an e-mail address, or a validation error (422)."""
    canonical = canonical_email(clean_text(value))  # control characters (NUL) are refused
    local, _, domain = canonical.rpartition("@")
    if (
        not local
        or "." not in domain
        or any(c.isspace() for c in canonical)
        or len(canonical) > MAX_EMAIL_LENGTH
    ):
        raise ValueError("not an e-mail address")
    return canonical


EmailAddress = Annotated[str, Field(min_length=3, max_length=320), AfterValidator(_email_address)]


class AuthMethod(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    PASSWORD = "PASSWORD"
    REGISTRATION = "REGISTRATION"


class DeviceKind(StrEnum):
    DESKTOP_BROWSER = "DESKTOP_BROWSER"
    MOBILE_BROWSER = "MOBILE_BROWSER"
    UNKNOWN = "UNKNOWN"


class TokenPurpose(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    INVITATION = "INVITATION"
    REGISTRATION = "REGISTRATION"


class AttemptKind(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    REDEEM = "REDEEM"
    PASSWORD = "PASSWORD"
    INVITATION = "INVITATION"


class SignInStatus(StrEnum):
    SIGNED_IN = "SIGNED_IN"
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"
    SIGN_IN_TO_ACCEPT = "SIGN_IN_TO_ACCEPT"


class InvitationKind(StrEnum):
    OWNER = "OWNER"
    TENANT = "TENANT"
    STAFF = "STAFF"


class StaffRole(StrEnum):
    """The building roles a STAFF invitation can give (USER is no staff role)."""

    BOARD = "BOARD"
    ACCOUNTANT = "ACCOUNTANT"
    CHAIRMAN = "CHAIRMAN"


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    NO_LONGER_VALID = "NO_LONGER_VALID"


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress


class MagicLinkAccepted(BaseModel):
    accepted: bool = True


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class SessionResponse(BaseModel):
    """What a sign-in or a refresh returns; the refresh cookie travels next to it."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    session_id: uuid.UUID


class SessionView(BaseModel):
    session_id: uuid.UUID
    device_kind: DeviceKind
    auth_method: AuthMethod
    authenticated_at: datetime
    last_used_at: datetime
    current: bool


@dataclass(frozen=True)
class IssuedSession:
    """A session just started or refreshed: the answer, and the refresh token for the cookie
    (None when the browser already holds the current one: the grace window)."""

    response: SessionResponse
    refresh_token: str | None
    expires_at: datetime  # of the session, for the cookie's Max-Age


class InvitationPreview(BaseModel):
    """What an invitation grants, shown to the holder of its link only."""

    kind: InvitationKind
    apartment_number: str | None
    building_role: StaffRole | None


class SignInResponse(BaseModel):
    """The answer of redeeming a link or registering (docs/API.md 7)."""

    status: SignInStatus
    session: SessionResponse | None = None
    # REGISTRATION_REQUIRED only:
    registration_token: str | None = None
    expires_in: int | None = None
    # REGISTRATION_REQUIRED and SIGN_IN_TO_ACCEPT:
    invitation: InvitationPreview | None = None


# --- Invitations (A4) ---------------------------------------------------------------------------


class OwnerInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[InvitationKind.OWNER]
    email: EmailAddress
    apartment_number: safe_text(32)  # type: ignore[valid-type]


class TenantInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[InvitationKind.TENANT]
    email: EmailAddress
    apartment_id: uuid.UUID


class StaffInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[InvitationKind.STAFF]
    email: EmailAddress
    building_role: StaffRole


InvitationCreate = Annotated[
    OwnerInvitationCreate | TenantInvitationCreate | StaffInvitationCreate,
    Field(discriminator="kind"),
]


class InvitationView(BaseModel):
    invitation_id: uuid.UUID
    kind: InvitationKind
    # In full only for the person who typed it in (`mine`), masked for everyone else.
    email: str
    apartment_id: uuid.UUID | None
    apartment_number: str | None
    building_role: StaffRole | None
    status: InvitationStatus
    mine: bool
    created_at: datetime
    expires_at: datetime


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_token: str = Field(min_length=1, max_length=128)
    full_name: safe_text(255)  # type: ignore[valid-type]
    phone: optional_safe_text(50) = None  # type: ignore[valid-type]
    locale: Locale
    # Ignored for STAFF invitations, which create no membership.
    privacy_display_mode: DisplayMode = DisplayMode.APARTMENT_ONLY


class AcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class AcceptResponse(BaseModel):
    applied: bool


class ReplaceTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress


class ResidentView(BaseModel):
    user_id: uuid.UUID
    membership_id: uuid.UUID
    relation_type: RelationType
    display_label: str
    since: datetime
    # The e-mail the caller typed when inviting this person into this membership, else null.
    invited_email: str | None


class ResidentsResponse(BaseModel):
    residents: list[ResidentView]
    pending_invitations: list[InvitationView]


class PersonMembershipView(BaseModel):
    membership_id: uuid.UUID
    apartment_id: uuid.UUID
    apartment_number: str
    relation_type: RelationType
    display_label: str
    since: datetime
    invited_email: str | None


class PersonView(BaseModel):
    user_id: uuid.UUID
    is_me: bool
    display_label: str
    building_role: BuildingRole
    # From the newest applied STAFF invitation for the current role that the caller typed.
    invited_email: str | None
    memberships: list[PersonMembershipView]


class PeopleResponse(BaseModel):
    people: list[PersonView]


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_role: BuildingRole


class RoleUpdateResponse(BaseModel):
    user_id: uuid.UUID
    building_role: BuildingRole
    account_closed: bool
