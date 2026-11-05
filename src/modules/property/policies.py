"""Pure rules of the property module (no database access; unit-tested directly): privacy
masking and the targeting rule shared with communication and notifications.

Badge texts are English, like everything the backend returns (see docs/API.md,
"Author badge rules").
"""

import uuid
from collections.abc import Collection, Iterable

from src.modules.property.schemas import (
    AuthorBadgeDTO,
    BuildingRole,
    DisplayMode,
    RelationType,
    RoleBadge,
    TargetScope,
)

APARTMENT_LABEL = "Apt."

# Shown instead of a name when there is no apartment context and the user keeps the default mode.
BUILDING_ROLE_LABELS: dict[BuildingRole, str] = {
    BuildingRole.CHAIRMAN: "Chairman",
    BuildingRole.BOARD: "Board",
    BuildingRole.ACCOUNTANT: "Accountant",
    BuildingRole.USER: "Resident",
}

_BOARD_ROLES = frozenset({BuildingRole.CHAIRMAN, BuildingRole.BOARD})

# Who sees the apartment cards (epic E8): the board, not the accountant (docs/API.md section 1).
CARD_VIEWER_ROLES = _BOARD_ROLES

# From most private to least private.
_PRIVACY_ORDER = (DisplayMode.APARTMENT_ONLY, DisplayMode.INITIALS, DisplayMode.FULL_NAME)


def most_private_display_mode(modes: Iterable[DisplayMode]) -> DisplayMode:
    """The strictest of the given modes (APARTMENT_ONLY when there are none).

    Used wherever no single apartment decides: consenting to be named in one apartment's
    context must never reveal the name in another.
    """
    return min(modes, key=_PRIVACY_ORDER.index, default=DisplayMode.APARTMENT_ONLY)


def format_initials(full_name: str) -> str:
    """'Ivan Petrov' -> 'I. P.'; extra whitespace is ignored."""
    return " ".join(f"{part[0].upper()}." for part in full_name.split())


def resolve_role_badge(
    relation_type: RelationType | None, building_role: BuildingRole
) -> RoleBadge:
    """Board membership takes precedence, so neighbours know they are talking to the board."""
    if building_role in _BOARD_ROLES:
        return RoleBadge.BOARD
    if building_role == BuildingRole.ACCOUNTANT:
        return RoleBadge.ACCOUNTANT
    if relation_type == RelationType.OWNER:
        return RoleBadge.OWNER
    return RoleBadge.RESIDENT


def format_author_badge(
    full_name: str,
    apt_number: str | None,
    relation_type: RelationType | None,
    building_role: BuildingRole,
    display_mode: DisplayMode,
) -> AuthorBadgeDTO:
    """Build the only representation of a person that other residents may ever see.

    APARTMENT_ONLY -> "Apt. 14"
    INITIALS       -> "I. P. (Apt. 14)"
    FULL_NAME      -> "Ivan Petrov (Apt. 14)"
    Without an apartment the name stands alone, or the building role label is used.
    """
    apartment = f"{APARTMENT_LABEL} {apt_number}" if apt_number else None

    name: str | None = None
    if display_mode == DisplayMode.INITIALS:
        name = format_initials(full_name) or None
    elif display_mode == DisplayMode.FULL_NAME:
        name = " ".join(full_name.split()) or None

    if name and apartment:
        label = f"{name} ({apartment})"
    else:
        label = name or apartment or BUILDING_ROLE_LABELS[building_role]

    return AuthorBadgeDTO(
        display_label=label,
        role_badge=resolve_role_badge(relation_type, building_role),
    )


def format_named_badge(full_name: str, building_role: BuildingRole) -> AuthorBadgeDTO:
    """The author of a board topic, as the board sees them: the full name, never an apartment.

    The one place a name is shown without the person's privacy choice (owner decision, epic E8):
    board members know each other and act publicly for the association. A blank name falls back
    to the role label.
    """
    return AuthorBadgeDTO(
        display_label=" ".join(full_name.split()) or BUILDING_ROLE_LABELS[building_role],
        role_badge=resolve_role_badge(None, building_role),
    )


def apartment_in_target(
    scope: TargetScope,
    target_entrance: int | None,
    target_apartment_ids: Collection[uuid.UUID],
    *,
    apartment_id: uuid.UUID,
    entrance: int,
) -> bool:
    """Whether an apartment is in the target of an alert or notice.

    The one rule for who is targeted: notification recipients use it directly, and the SQL filter
    of communication's post visibility is kept equal to it by tests.
    """
    if scope == TargetScope.ALL_BUILDING:
        return True
    if scope == TargetScope.ENTRANCE:
        return target_entrance is not None and entrance == target_entrance
    return apartment_id in target_apartment_ids
