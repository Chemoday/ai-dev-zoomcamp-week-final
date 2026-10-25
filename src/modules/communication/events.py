"""Domain events of the communication module, published on core.event_bus after commit.

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
