"""Unit tests for the pure privacy masker (no database)."""

import pytest

from src.modules.property.policies import format_author_badge, format_initials, resolve_role_badge
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType, RoleBadge


def badge(mode: DisplayMode, **overrides):
    arguments = {
        "full_name": "Иван Петров",
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

    assert result.display_label == "Кв. 14"
    assert result.role_badge == RoleBadge.OWNER


def test_initials_show_initials_and_apartment() -> None:
    assert badge(DisplayMode.INITIALS).display_label == "И. П. (Кв. 14)"


def test_full_name_shows_name_and_apartment() -> None:
    assert badge(DisplayMode.FULL_NAME).display_label == "Иван Петров (Кв. 14)"


def test_apartment_only_never_contains_any_part_of_the_name() -> None:
    label = badge(DisplayMode.APARTMENT_ONLY).display_label

    assert "Иван" not in label
    assert "Петров" not in label
    assert "И." not in label


# --- Edge cases ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Иван Петров", "И. П."),
        ("  иван   петров  ", "И. П."),
        ("Анна-Мария Тамм", "А. Т."),
        ("Иван Иванович Петров", "И. И. П."),
        ("Мадонна", "М."),
        ("", ""),
    ],
)
def test_format_initials(full_name: str, expected: str) -> None:
    assert format_initials(full_name) == expected


def test_full_name_normalizes_whitespace() -> None:
    label = badge(DisplayMode.FULL_NAME, full_name="  Иван   Петров ").display_label
    assert label == "Иван Петров (Кв. 14)"


def test_blank_name_falls_back_to_apartment() -> None:
    assert badge(DisplayMode.INITIALS, full_name="   ").display_label == "Кв. 14"
    assert badge(DisplayMode.FULL_NAME, full_name="").display_label == "Кв. 14"


@pytest.mark.parametrize(
    ("role", "mode", "expected_label"),
    [
        (BuildingRole.CHAIRMAN, DisplayMode.APARTMENT_ONLY, "Председатель"),
        (BuildingRole.BOARD, DisplayMode.APARTMENT_ONLY, "Правление"),
        (BuildingRole.ACCOUNTANT, DisplayMode.APARTMENT_ONLY, "Бухгалтер"),
        (BuildingRole.CHAIRMAN, DisplayMode.INITIALS, "И. П."),
        (BuildingRole.CHAIRMAN, DisplayMode.FULL_NAME, "Иван Петров"),
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
    result = format_author_badge("Иван Петров", "14", "OWNER", "USER", "INITIALS")
    assert result.display_label == "И. П. (Кв. 14)"
    assert result.role_badge == RoleBadge.OWNER
