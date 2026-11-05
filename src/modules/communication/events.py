"""Domain events of the communication module, published on the app's event bus after commit.

Payloads carry ids, codes and the target only: no names, and no free text (titles and bodies),
so a handler that logs an event can never leak what a neighbour wrote. A notifier that needs the
text reads the post through CommunicationService.
"""

import uuid
from dataclasses import dataclass

from src.core.event_bus import DomainEvent
from src.modules.communication.schemas import PostCategory, TargetScope


@dataclass(frozen=True, kw_only=True)
class EmergencyAlertPublished(DomainEvent):
    """An EMERGENCY post was created; notifiers (Telegram, WebPush) deliver it to its target."""

    post_id: uuid.UUID
    building_id: uuid.UUID
    category: PostCategory
    target_scope: TargetScope
    target_entrance: int | None  # set for ENTRANCE
    target_apartment_ids: tuple[uuid.UUID, ...]  # set for APARTMENT_LIST, empty otherwise
    published_by_user_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class EmergencyAlertResolved(DomainEvent):
    """An active emergency was marked resolved; its banner disappears for everyone."""

    post_id: uuid.UUID
    building_id: uuid.UUID
    resolved_by_user_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class ApartmentMessagePosted(DomainEvent):
    """A board <-> apartment thread was started (comment_id None) or answered (epic E8).

    Recipients are not in the payload: the notifier asks CommunicationService who sees the
    thread, so the one visibility rule decides.
    """

    post_id: uuid.UUID
    comment_id: uuid.UUID | None
    building_id: uuid.UUID
    apartment_id: uuid.UUID
    author_user_id: uuid.UUID
    from_board: bool  # the author wrote as the board, without an apartment
