"""Unit tests of the pure communication rules (no database)."""

import uuid

import pytest

from src.modules.communication.policies import can_publish, sees_all_targets, target_problem
from src.modules.communication.schemas import PostType, TargetScope
from src.modules.property.schemas import BuildingRole

APARTMENT = uuid.uuid4()


@pytest.mark.parametrize(
    ("post_type", "role", "allowed"),
    [
        (PostType.EMERGENCY, BuildingRole.CHAIRMAN, True),
        (PostType.EMERGENCY, BuildingRole.BOARD, True),
        (PostType.EMERGENCY, BuildingRole.ACCOUNTANT, False),
        (PostType.EMERGENCY, BuildingRole.USER, False),
        (PostType.OFFICIAL_NOTICE, BuildingRole.CHAIRMAN, True),
        (PostType.OFFICIAL_NOTICE, BuildingRole.BOARD, True),
        (PostType.OFFICIAL_NOTICE, BuildingRole.ACCOUNTANT, False),
        (PostType.OFFICIAL_NOTICE, BuildingRole.USER, False),
        (PostType.FORUM_TOPIC, BuildingRole.USER, True),
        (PostType.FORUM_TOPIC, BuildingRole.ACCOUNTANT, True),
        (PostType.FORUM_TOPIC, BuildingRole.CHAIRMAN, True),
    ],
)
def test_who_may_publish(post_type: PostType, role: BuildingRole, allowed: bool) -> None:
    assert can_publish(post_type, role) is allowed


def test_only_the_board_sees_every_target() -> None:
    assert sees_all_targets(BuildingRole.CHAIRMAN)
    assert sees_all_targets(BuildingRole.BOARD)
    assert not sees_all_targets(BuildingRole.ACCOUNTANT)
    assert not sees_all_targets(BuildingRole.USER)


@pytest.mark.parametrize(
    ("post_type", "scope", "entrance", "apartments"),
    [
        (PostType.EMERGENCY, TargetScope.ALL_BUILDING, None, None),
        (PostType.EMERGENCY, TargetScope.ALL_BUILDING, None, []),
        (PostType.EMERGENCY, TargetScope.ENTRANCE, 2, None),
        (PostType.EMERGENCY, TargetScope.APARTMENT_LIST, None, [APARTMENT]),
        (PostType.OFFICIAL_NOTICE, TargetScope.ENTRANCE, 1, None),
        (PostType.FORUM_TOPIC, TargetScope.ALL_BUILDING, None, None),
    ],
)
def test_consistent_targets_pass(post_type, scope, entrance, apartments) -> None:
    assert target_problem(post_type, scope, entrance, apartments) is None


@pytest.mark.parametrize(
    ("post_type", "scope", "entrance", "apartments", "fragment"),
    [
        (PostType.FORUM_TOPIC, TargetScope.ENTRANCE, 2, None, "whole building"),
        (PostType.FORUM_TOPIC, TargetScope.APARTMENT_LIST, None, [APARTMENT], "whole building"),
        (PostType.EMERGENCY, TargetScope.ALL_BUILDING, 2, None, "ALL_BUILDING"),
        (PostType.EMERGENCY, TargetScope.ALL_BUILDING, None, [APARTMENT], "ALL_BUILDING"),
        (PostType.EMERGENCY, TargetScope.ENTRANCE, None, None, "needs target_entrance"),
        (PostType.EMERGENCY, TargetScope.ENTRANCE, 2, [APARTMENT], "does not take"),
        (PostType.EMERGENCY, TargetScope.APARTMENT_LIST, None, None, "at least one"),
        (PostType.EMERGENCY, TargetScope.APARTMENT_LIST, None, [], "at least one"),
        (PostType.EMERGENCY, TargetScope.APARTMENT_LIST, 2, [APARTMENT], "does not take"),
    ],
)
def test_inconsistent_targets_are_explained(post_type, scope, entrance, apartments, fragment):
    problem = target_problem(post_type, scope, entrance, apartments)

    assert problem is not None
    assert fragment in problem
