"""Unit tests for the pure privacy masker (no database)."""

import pytest

from src.modules.property.policies import (
    format_author_badge,
    format_initials,
    most_private_display_mode,
    resolve_role_badge,
)
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType, RoleBadge


def badge(mode: DisplayMode, **overrides):
    arguments = {
        "full_name": "Ivan Petrov",
        "apt_number": "14",
        "relation_type": RelationType.OWNER,
        "building_role": BuildingRole.USER,
        "display_mode": mode,
    }
    arguments.update(overrides)
    return format_author_badge(**arguments)


# --- Acceptance criteria (Task 2) ---------------------------------------------------------------


def test_apartment_only_shows_just_the_apartment() -> None:
    result = badge(DisplayMode.APARTMENT_ONLY)

    assert result.display_label == "Apt. 14"
    assert result.role_badge == RoleBadge.OWNER


def test_initials_show_initials_and_apartment() -> None:
    assert badge(DisplayMode.INITIALS).display_label == "I. P. (Apt. 14)"


def test_full_name_shows_name_and_apartment() -> None:
    assert badge(DisplayMode.FULL_NAME).display_label == "Ivan Petrov (Apt. 14)"


def test_apartment_only_never_contains_any_part_of_the_name() -> None:
    label = badge(DisplayMode.APARTMENT_ONLY).display_label

    assert "Ivan" not in label
    assert "Petrov" not in label
    assert "I." not in label


# --- Edge cases ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Ivan Petrov", "I. P."),
        ("  ivan   petrov  ", "I. P."),
        ("Anna-Maria Tamm", "A. T."),
        ("Ivan Ivanovich Petrov", "I. I. P."),
        ("Madonna", "M."),
        ("", ""),
    ],
)
def test_format_initials(full_name: str, expected: str) -> None:
    assert format_initials(full_name) == expected


def test_full_name_normalizes_whitespace() -> None:
    label = badge(DisplayMode.FULL_NAME, full_name="  Ivan   Petrov ").display_label
    assert label == "Ivan Petrov (Apt. 14)"


def test_blank_name_falls_back_to_apartment() -> None:
    assert badge(DisplayMode.INITIALS, full_name="   ").display_label == "Apt. 14"
    assert badge(DisplayMode.FULL_NAME, full_name="").display_label == "Apt. 14"


@pytest.mark.parametrize(
    ("role", "mode", "expected_label"),
    [
        (BuildingRole.CHAIRMAN, DisplayMode.APARTMENT_ONLY, "Chairman"),
        (BuildingRole.BOARD, DisplayMode.APARTMENT_ONLY, "Board"),
        (BuildingRole.ACCOUNTANT, DisplayMode.APARTMENT_ONLY, "Accountant"),
        (BuildingRole.CHAIRMAN, DisplayMode.INITIALS, "I. P."),
        (BuildingRole.CHAIRMAN, DisplayMode.FULL_NAME, "Ivan Petrov"),
    ],
)
def test_staff_without_apartment(
    role: BuildingRole, mode: DisplayMode, expected_label: str
) -> None:
    result = badge(mode, apt_number=None, relation_type=None, building_role=role)
    assert result.display_label == expected_label


@pytest.mark.parametrize(
    ("relation", "role", "expected"),
    [
        (RelationType.OWNER, BuildingRole.USER, RoleBadge.OWNER),
        (RelationType.TENANT, BuildingRole.USER, RoleBadge.RESIDENT),
        (RelationType.RESIDENT, BuildingRole.USER, RoleBadge.RESIDENT),
        (None, BuildingRole.USER, RoleBadge.RESIDENT),
        # Board membership wins over apartment relation: neighbours see who speaks for the board.
        (RelationType.OWNER, BuildingRole.CHAIRMAN, RoleBadge.BOARD),
        (RelationType.TENANT, BuildingRole.BOARD, RoleBadge.BOARD),
        (None, BuildingRole.ACCOUNTANT, RoleBadge.ACCOUNTANT),
    ],
)
def test_role_badge(relation, role, expected) -> None:
    assert resolve_role_badge(relation, role) == expected


def test_plain_string_inputs_are_accepted() -> None:
    result = format_author_badge("Ivan Petrov", "14", "OWNER", "USER", "INITIALS")
    assert result.display_label == "I. P. (Apt. 14)"
    assert result.role_badge == RoleBadge.OWNER


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ([], DisplayMode.APARTMENT_ONLY),
        ([DisplayMode.FULL_NAME], DisplayMode.FULL_NAME),
        ([DisplayMode.FULL_NAME, DisplayMode.INITIALS], DisplayMode.INITIALS),
        ([DisplayMode.FULL_NAME, DisplayMode.APARTMENT_ONLY], DisplayMode.APARTMENT_ONLY),
        (["FULL_NAME", "INITIALS"], DisplayMode.INITIALS),
    ],
)
def test_most_private_display_mode(modes, expected) -> None:
    assert most_private_display_mode(modes) == expected
