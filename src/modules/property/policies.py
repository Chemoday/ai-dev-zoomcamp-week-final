"""Pure privacy-masking rules (no database access; unit-tested directly).

Badge texts are English, like everything the backend returns (see docs/API.md,
"Author badge rules").
"""

from collections.abc import Iterable

from src.modules.property.schemas import (
    AuthorBadgeDTO,
    BuildingRole,
    DisplayMode,
    RelationType,
    RoleBadge,
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
