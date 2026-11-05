"""Pure rules of the communication module (no database access; unit-tested directly)."""

from collections.abc import Collection
from uuid import UUID

from src.modules.communication.schemas import PostType, TargetScope
from src.modules.property.schemas import BuildingRole

# Publish emergencies and official notices, resolve emergencies, and see every targeted post.
PUBLISHER_ROLES = frozenset({BuildingRole.CHAIRMAN, BuildingRole.BOARD})

# Delete forum topics (moderation).
MODERATOR_ROLES = frozenset({BuildingRole.CHAIRMAN})

_BOARD_ONLY_TYPES = frozenset({PostType.EMERGENCY, PostType.OFFICIAL_NOTICE})

# Private conversations: listed only when asked for by post_type, never in the forum feed.
FEED_HIDDEN_TYPES = frozenset({PostType.APARTMENT_THREAD})


def can_publish(post_type: PostType, building_role: BuildingRole) -> bool:
    """Emergencies and official notices come from the board; anyone may open a forum topic.

    Anyone may start a thread too; that a non-board author is a member of the thread's
    apartment is checked by the service against their memberships.
    """
    return post_type not in _BOARD_ONLY_TYPES or building_role in PUBLISHER_ROLES


def sees_all_targets(building_role: BuildingRole) -> bool:
    """The board sees every post of the building, including alerts targeted elsewhere."""
    return building_role in PUBLISHER_ROLES


def target_problem(
    post_type: PostType,
    target_scope: TargetScope,
    target_entrance: int | None,
    target_apartment_ids: Collection[UUID] | None,
) -> str | None:
    """Why the target of a new post is inconsistent, or None when it is fine.

    Whether the entrance or the apartments exist in the building is checked by the service.
    """
    if post_type == PostType.FORUM_TOPIC and target_scope != TargetScope.ALL_BUILDING:
        return "Forum topics are always visible to the whole building."
    if post_type == PostType.APARTMENT_THREAD and (
        target_scope != TargetScope.APARTMENT_LIST or len(target_apartment_ids or ()) != 1
    ):
        return "A thread with the board targets exactly one apartment (APARTMENT_LIST)."
    if target_scope == TargetScope.ALL_BUILDING:
        if target_entrance is not None or target_apartment_ids:
            return "ALL_BUILDING takes neither target_entrance nor target_apartment_ids."
    elif target_scope == TargetScope.ENTRANCE:
        if target_entrance is None:
            return "ENTRANCE needs target_entrance."
        if target_apartment_ids:
            return "ENTRANCE does not take target_apartment_ids."
    elif target_scope == TargetScope.APARTMENT_LIST:
        if not target_apartment_ids:
            return "APARTMENT_LIST needs at least one apartment in target_apartment_ids."
        if target_entrance is not None:
            return "APARTMENT_LIST does not take target_entrance."
    return None


def is_deletable(post_type: PostType) -> bool:
    """Only forum topics are deleted. Emergencies are resolved instead, and official notices
    and threads stay as the board's record."""
    return post_type == PostType.FORUM_TOPIC


def thread_author_apartment(
    thread_apartment_id: UUID, member_apartment_ids: Collection[UUID]
) -> UUID | None:
    """Who writes as whom in a thread: a member of its apartment writes from it, anyone else
    (the board) without an apartment. The server decides; the client cannot choose."""
    return thread_apartment_id if thread_apartment_id in member_apartment_ids else None
