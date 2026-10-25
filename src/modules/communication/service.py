"""Public facade of the communication module: emergency alerts, official notices and the forum.

Rules live in docs/API.md (section 4) and docs/ROADMAP_MVP.md (section D). Apartments and author
badges come from PropertyService; property models are never imported or joined.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import ColumnElement, Text, and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession, get_event_outbox
from src.core.event_bus import EventOutbox
from src.core.exceptions import DomainError
from src.modules.communication.events import EmergencyAlertPublished, EmergencyAlertResolved
from src.modules.communication.models import Comment, EmergencyAck, Post
from src.modules.communication.policies import can_publish, sees_all_targets, target_problem
from src.modules.communication.schemas import (
    AcknowledgeResponse,
    CommentCreateRequest,
    CommentView,
    EmergencyView,
    PostCategory,
    PostCreateRequest,
    PostDetail,
    PostType,
    PostView,
    ResolveResponse,
    TargetScope,
)
from src.modules.property.schemas import AuthorBadgeDTO, CurrentUserDTO
from src.modules.property.service import AuthorKey, PropertyService, get_property_service


def _post_not_found() -> DomainError:
    # The same answer for "does not exist" and "not targeted at you", so ids cannot be probed.
    return DomainError(status_code=404, error_code="POST_NOT_FOUND", message="Post not found.")


def _invalid_target(message: str, **extra: object) -> DomainError:
    return DomainError(status_code=422, error_code="INVALID_TARGET", message=message, extra=extra)


def _author_key(item: Post | Comment) -> AuthorKey:
    return (item.author_user_id, item.author_apartment_id)


class CommunicationService:
    def __init__(
        self, session: AsyncSession, property_service: PropertyService, outbox: EventOutbox
    ):
        self.session = session
        self.property_service = property_service
        # Events go out only after the request's transaction commits (docs/BACKEND_CONVENTIONS.md).
        self.outbox = outbox

    # --- Emergencies ----------------------------------------------------------------------------

    async def list_active_emergencies(self, user: CurrentUserDTO) -> list[EmergencyView]:
        """Active emergencies targeted at the caller, newest first, with their own ack time."""
        posts = list(
            await self.session.scalars(
                select(Post)
                .where(
                    self._visible_to(user),
                    Post.post_type == PostType.EMERGENCY,
                    Post.is_active.is_(True),
                )
                .order_by(Post.created_at.desc(), Post.id.desc())
            )
        )
        acks: dict[uuid.UUID, datetime] = {}
        if posts:
            acks = dict(
                (
                    await self.session.execute(
                        select(EmergencyAck.post_id, EmergencyAck.acknowledged_at).where(
                            EmergencyAck.user_id == user.id,
                            EmergencyAck.post_id.in_([post.id for post in posts]),
                        )
                    )
                ).all()
            )
        views = await self._post_views(user, posts)
        return [
            EmergencyView(**view.model_dump(), acknowledged_at=acks.get(view.id)) for view in views
        ]

    async def acknowledge_emergency(
        self, user: CurrentUserDTO, post_id: uuid.UUID
    ) -> AcknowledgeResponse:
        """Idempotent: acknowledging again keeps (and returns) the first acknowledgement."""
        post = await self._visible_post(user, post_id)
        if post.post_type != PostType.EMERGENCY:
            raise _post_not_found()
        await self.session.execute(
            pg_insert(EmergencyAck)
            .values(
                id=uuid.uuid4(),
                post_id=post.id,
                user_id=user.id,
                acknowledged_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_comm_emergency_acks_post_user")
        )
        acknowledged_at = await self.session.scalar(
            select(EmergencyAck.acknowledged_at).where(
                EmergencyAck.post_id == post.id, EmergencyAck.user_id == user.id
            )
        )
        return AcknowledgeResponse(post_id=post.id, acknowledged_at=acknowledged_at)

    async def resolve_emergency(self, user: CurrentUserDTO, post_id: uuid.UUID) -> ResolveResponse:
        """Idempotent: only the call that actually ends the emergency emits the event.

        A single conditional UPDATE, so two board members resolving at once emit one event.
        """
        is_emergency = and_(
            Post.id == post_id,
            Post.building_id == user.building_id,
            Post.post_type == PostType.EMERGENCY,
        )
        resolved = await self.session.scalar(
            update(Post)
            .where(is_emergency, Post.is_active.is_(True))
            .values(is_active=False)
            .returning(Post.id)
        )
        if resolved is None:
            if await self.session.scalar(select(Post.id).where(is_emergency)) is None:
                raise _post_not_found()
        else:
            self.outbox.add(
                EmergencyAlertResolved(
                    post_id=post_id, building_id=user.building_id, resolved_by_user_id=user.id
                )
            )
        return ResolveResponse(post_id=post_id, is_active=False)

    # --- Feed and posts -------------------------------------------------------------------------

    async def list_posts(
        self,
        user: CurrentUserDTO,
        post_type: PostType | None,
        category: PostCategory | None,
        limit: int,
        offset: int,
    ) -> list[PostView]:
        statement = select(Post).where(self._visible_to(user))
        if post_type is not None:
            statement = statement.where(Post.post_type == post_type)
        if category is not None:
            statement = statement.where(Post.category == category)
        posts = list(
            await self.session.scalars(
                statement.order_by(Post.created_at.desc(), Post.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return await self._post_views(user, posts)

    async def get_post(self, user: CurrentUserDTO, post_id: uuid.UUID) -> PostDetail:
        post = await self._visible_post(user, post_id)
        comments = list(
            await self.session.scalars(
                select(Comment)
                .where(Comment.post_id == post.id)
                .order_by(Comment.created_at, Comment.id)
            )
        )
        badges = await self.property_service.get_masked_author_badges(
            [_author_key(post), *(_author_key(comment) for comment in comments)]
        )
        return PostDetail(
            **self._post_view(user, post, badges[_author_key(post)], len(comments)).model_dump(),
            comments=[
                self._comment_view(user, comment, badges[_author_key(comment)])
                for comment in comments
            ],
        )

    async def create_post(self, user: CurrentUserDTO, request: PostCreateRequest) -> PostView:
        if not can_publish(request.post_type, user.building_role):
            raise DomainError(
                status_code=403,
                error_code="FORBIDDEN",
                message="Only the board publishes emergencies and official notices.",
            )
        if problem := target_problem(
            request.post_type,
            request.target_scope,
            request.target_entrance,
            request.target_apartment_ids,
        ):
            raise _invalid_target(problem)
        await self._check_target_exists(user, request)
        author_apartment_id = self._author_apartment(
            user,
            request.author_apartment_id,
            default_to_membership=request.post_type == PostType.FORUM_TOPIC,
        )

        post = Post(
            building_id=user.building_id,
            author_user_id=user.id,
            author_apartment_id=author_apartment_id,
            post_type=request.post_type,
            category=request.category,
            title=request.title,
            body=request.body,
            target_scope=request.target_scope,
            target_entrance=request.target_entrance,
            target_apartment_ids=(
                [str(apartment_id) for apartment_id in request.target_apartment_ids]
                if request.target_apartment_ids
                else None
            ),
            comments_enabled=request.comments_enabled,
        )
        self.session.add(post)
        await self.session.flush()

        if post.post_type == PostType.EMERGENCY:
            self.outbox.add(
                EmergencyAlertPublished(
                    post_id=post.id,
                    building_id=post.building_id,
                    category=post.category,
                    target_scope=post.target_scope,
                    target_entrance=post.target_entrance,
                    target_apartment_ids=tuple(request.target_apartment_ids or ()),
                    published_by_user_id=user.id,
                )
            )
        badge = await self.property_service.get_masked_author_badge(user.id, author_apartment_id)
        return self._post_view(user, post, badge, comments_count=0)

    async def add_comment(
        self, user: CurrentUserDTO, post_id: uuid.UUID, request: CommentCreateRequest
    ) -> CommentView:
        post = await self._visible_post(user, post_id)
        if not post.comments_enabled:
            raise DomainError(
                status_code=409,
                error_code="COMMENTS_DISABLED",
                message="Comments are disabled for this post.",
            )
        author_apartment_id = self._author_apartment(
            user, request.author_apartment_id, default_to_membership=True
        )
        comment = Comment(
            post_id=post.id,
            building_id=post.building_id,
            author_user_id=user.id,
            author_apartment_id=author_apartment_id,
            body=request.body,
        )
        self.session.add(comment)
        await self.session.flush()
        badge = await self.property_service.get_masked_author_badge(user.id, author_apartment_id)
        return self._comment_view(user, comment, badge)

    # --- Internals ------------------------------------------------------------------------------

    @staticmethod
    def _visible_to(user: CurrentUserDTO) -> ColumnElement[bool]:
        """Posts of the caller's building that target the caller (the board sees all of them).

        ENTRANCE matches any entrance of the caller's apartments, APARTMENT_LIST any of the
        caller's apartments, so a user with two apartments sees the alerts of both.
        """
        in_building = Post.building_id == user.building_id
        if sees_all_targets(user.building_role):
            return in_building
        targeted: list[ColumnElement[bool]] = [Post.target_scope == TargetScope.ALL_BUILDING]
        if entrances := sorted({membership.entrance for membership in user.memberships}):
            targeted.append(
                and_(Post.target_scope == TargetScope.ENTRANCE, Post.target_entrance.in_(entrances))
            )
        if apartment_ids := sorted(str(apartment_id) for apartment_id in user.apartment_ids):
            targeted.append(
                and_(
                    Post.target_scope == TargetScope.APARTMENT_LIST,
                    # JSONB ?| : the list holds at least one of the caller's apartment ids.
                    Post.target_apartment_ids.has_any(literal(apartment_ids, ARRAY(Text))),
                )
            )
        return and_(in_building, or_(*targeted))

    async def _visible_post(self, user: CurrentUserDTO, post_id: uuid.UUID) -> Post:
        post = await self.session.scalar(
            select(Post).where(Post.id == post_id, self._visible_to(user))
        )
        if post is None:
            raise _post_not_found()
        return post

    async def _check_target_exists(self, user: CurrentUserDTO, request: PostCreateRequest) -> None:
        if request.target_scope == TargetScope.ALL_BUILDING:
            return
        apartments = await self.property_service.list_apartments(user.building_id)
        if request.target_scope == TargetScope.ENTRANCE:
            if request.target_entrance not in {apartment.entrance for apartment in apartments}:
                raise _invalid_target(
                    f"The building has no apartments in entrance {request.target_entrance}."
                )
        elif unknown := set(request.target_apartment_ids or ()) - {a.id for a in apartments}:
            raise _invalid_target(
                "Some target apartments are not in this building.",
                unknown_apartment_ids=sorted(str(apartment_id) for apartment_id in unknown),
            )

    @staticmethod
    def _author_apartment(
        user: CurrentUserDTO, requested: uuid.UUID | None, default_to_membership: bool
    ) -> uuid.UUID | None:
        """The apartment whose privacy mode decides the author's badge.

        An explicit choice must be one of the caller's own apartments. Without one, forum
        topics and comments use the caller's first apartment, and board posts none (the board
        speaks as the board).
        """
        if requested is not None:
            if user.membership_for(requested) is None:
                raise DomainError(
                    status_code=422,
                    error_code="INVALID_AUTHOR_APARTMENT",
                    message="You can only write on behalf of an apartment you belong to.",
                )
            return requested
        if default_to_membership and user.memberships:
            return user.memberships[0].apartment_id
        return None

    async def _post_views(self, user: CurrentUserDTO, posts: list[Post]) -> list[PostView]:
        if not posts:
            return []
        counts = await self._comment_counts(post.id for post in posts)
        badges = await self.property_service.get_masked_author_badges(
            _author_key(post) for post in posts
        )
        return [
            self._post_view(user, post, badges[_author_key(post)], counts.get(post.id, 0))
            for post in posts
        ]

    async def _comment_counts(self, post_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        rows = await self.session.execute(
            select(Comment.post_id, func.count())
            .where(Comment.post_id.in_(list(post_ids)))
            .group_by(Comment.post_id)
        )
        return {post_id: count for post_id, count in rows}

    @staticmethod
    def _post_view(
        user: CurrentUserDTO, post: Post, badge: AuthorBadgeDTO, comments_count: int
    ) -> PostView:
        return PostView(
            id=post.id,
            post_type=post.post_type,
            category=post.category,
            title=post.title,
            body=post.body,
            author_badge=badge,
            is_mine=post.author_user_id == user.id,
            target_scope=post.target_scope,
            target_entrance=post.target_entrance,
            target_apartment_ids=(
                [uuid.UUID(value) for value in post.target_apartment_ids]
                if post.target_apartment_ids is not None
                else None
            ),
            is_active=post.is_active,
            comments_enabled=post.comments_enabled,
            comments_count=comments_count,
            created_at=post.created_at,
        )

    @staticmethod
    def _comment_view(user: CurrentUserDTO, comment: Comment, badge: AuthorBadgeDTO) -> CommentView:
        return CommentView(
            id=comment.id,
            post_id=comment.post_id,
            body=comment.body,
            author_badge=badge,
            is_mine=comment.author_user_id == user.id,
            created_at=comment.created_at,
        )


def get_communication_service(
    session: DbSession,
    property_service: PropertyService = Depends(get_property_service),
    outbox: EventOutbox = Depends(get_event_outbox),
) -> CommunicationService:
    return CommunicationService(session, property_service, outbox)
