"""Public facade of the communication module: emergency alerts, official notices, the forum and
the board <-> apartment threads (epic E8).

Rules live in docs/API.md (section 4) and docs/ROADMAP_MVP.md (section D). Apartments and author
badges come from PropertyService; property models are never imported or joined.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import (
    ColumnElement,
    Text,
    and_,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession, get_event_outbox
from src.core.event_bus import EventOutbox
from src.core.exceptions import DomainError
from src.modules.communication.events import (
    ApartmentMessagePosted,
    EmergencyAlertPublished,
    EmergencyAlertResolved,
    PostDeleted,
)
from src.modules.communication.models import Comment, EmergencyAck, Post
from src.modules.communication.policies import (
    ACTIVITY_SORTED_TYPES,
    FEED_HIDDEN_TYPES,
    can_publish,
    deletable_types,
    is_deletable,
    sees_all_targets,
    target_problem,
    thread_author_apartment,
)
from src.modules.communication.schemas import (
    AcknowledgeResponse,
    CommentCreateRequest,
    CommentView,
    EmergencyRefDTO,
    EmergencyView,
    PostCategory,
    PostCreateRequest,
    PostDetail,
    PostType,
    PostView,
    ResolveResponse,
    TargetScope,
    ThreadRefDTO,
)
from src.modules.property.schemas import AuthorBadgeDTO, CurrentUserDTO
from src.modules.property.service import AuthorKey, PropertyService, get_property_service


def _post_not_found() -> DomainError:
    # The same answer for "does not exist" and "not targeted at you", so ids cannot be probed.
    return DomainError(status_code=404, error_code="POST_NOT_FOUND", message="Post not found.")


def _invalid_target(message: str, **extra: object) -> DomainError:
    return DomainError(status_code=422, error_code="INVALID_TARGET", message=message, extra=extra)


def _invalid_author_apartment() -> DomainError:
    return DomainError(
        status_code=422,
        error_code="INVALID_AUTHOR_APARTMENT",
        message="You can only write on behalf of an apartment you belong to.",
    )


def _author_key(item: Post | Comment) -> AuthorKey:
    return (item.author_user_id, item.author_apartment_id)


def _thread_apartment(post: Post) -> uuid.UUID:
    (apartment_id,) = post.target_apartment_ids or ()  # exactly one, a database check
    return uuid.UUID(apartment_id)


class CommunicationService:
    def __init__(
        self,
        session: AsyncSession,
        property_service: PropertyService,
        outbox: EventOutbox | None = None,
    ):
        self.session = session
        self.property_service = property_service
        # Events go out only after the request's transaction commits (docs/BACKEND_CONVENTIONS.md).
        # None only for system-level readers (e.g. notifications), which never write.
        self.outbox = outbox

    # --- System-level reads (no caller; used by the notifications module) -----------------------

    async def get_emergency_ref(
        self, post_id: uuid.UUID, *, lock_for_share: bool = False
    ) -> EmergencyRefDTO | None:
        """An emergency regardless of its status, or None if there is no such emergency.

        `lock_for_share` keeps the row locked until the caller's transaction ends, so a
        concurrent resolve waits: notifications use it to store "published" rows before the
        alert can be resolved (a resolved event then always finds them).
        """
        query = select(Post).where(Post.id == post_id, Post.post_type == PostType.EMERGENCY)
        if lock_for_share:
            # Refresh an object the session may already hold, so the locked state is the one read.
            query = query.with_for_update(read=True).execution_options(populate_existing=True)
        post = await self.session.scalar(query)
        if post is None:
            return None
        return EmergencyRefDTO(
            post_id=post.id,
            building_id=post.building_id,
            author_user_id=post.author_user_id,
            is_active=post.is_active,
            target_scope=post.target_scope,
            target_entrance=post.target_entrance,
            target_apartment_ids=tuple(uuid.UUID(a) for a in post.target_apartment_ids or ()),
            title=post.title,
            body=post.body,
            category=post.category,
        )

    async def get_thread_ref(self, post_id: uuid.UUID) -> ThreadRefDTO | None:
        """A board <-> apartment thread, or None if there is no such thread."""
        post = await self.session.scalar(
            select(Post).where(Post.id == post_id, Post.post_type == PostType.APARTMENT_THREAD)
        )
        if post is None:
            return None
        return ThreadRefDTO(
            post_id=post.id, building_id=post.building_id, apartment_id=_thread_apartment(post)
        )

    async def can_read_post(self, user: CurrentUserDTO, post_id: uuid.UUID) -> bool:
        """Whether the user sees the post under the visibility rule of the feed and the API.

        Notifications ask it for the recipients of a thread message, and again before sending.
        """
        visible = await self.session.scalar(
            select(Post.id).where(Post.id == post_id, self._visible_to(user))
        )
        return visible is not None

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
            assert self.outbox is not None, "writes need the request outbox"
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
        apartment_id: uuid.UUID | None = None,
    ) -> list[PostView]:
        """The feed, newest first; without post_type it leaves threads and board topics out.

        `post_type=APARTMENT_THREAD` (the inbox) and `BOARD_TOPIC` (the board's micro-forum) are
        sorted by the newest activity; only the inbox takes `apartment_id` (the router rejects
        it otherwise).
        """
        statement = select(Post).where(self._visible_to(user))
        if post_type is None:
            statement = statement.where(Post.post_type.not_in(sorted(FEED_HIDDEN_TYPES)))
        else:
            statement = statement.where(Post.post_type == post_type)
        if category is not None:
            statement = statement.where(Post.category == category)
        if apartment_id is not None:
            statement = statement.where(Post.target_apartment_ids.contains([str(apartment_id)]))
        by_activity = post_type in ACTIVITY_SORTED_TYPES
        newest = Post.last_activity_at if by_activity else Post.created_at
        posts = list(
            await self.session.scalars(
                statement.order_by(newest.desc(), Post.id.desc()).limit(limit).offset(offset)
            )
        )
        return await self._post_views(user, posts)

    async def get_post(
        self, user: CurrentUserDTO, post_id: uuid.UUID, comments_limit: int, comments_offset: int
    ) -> PostDetail:
        """The post with one page of its comments, oldest first; comments_count is the total."""
        post = await self._visible_post(user, post_id)
        comments = list(
            await self.session.scalars(
                select(Comment)
                .where(Comment.post_id == post.id)
                .order_by(Comment.created_at, Comment.id)
                .limit(comments_limit)
                .offset(comments_offset)
            )
        )
        total = (await self._comment_counts([post.id])).get(post.id, 0)
        badges = await self._badges(
            user.building_id,
            post.post_type == PostType.BOARD_TOPIC,
            [_author_key(post), *(_author_key(comment) for comment in comments)],
        )
        return PostDetail(
            **self._post_view(user, post, badges[_author_key(post)], total).model_dump(),
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
                message="Only the board publishes emergencies, official notices and board topics.",
            )
        if problem := target_problem(
            request.post_type,
            request.target_scope,
            request.target_entrance,
            request.target_apartment_ids,
        ):
            raise _invalid_target(problem)
        await self._check_target_exists(user, request)
        if request.post_type == PostType.APARTMENT_THREAD:
            author_apartment_id = await self._check_new_thread(user, request)
        elif request.post_type == PostType.BOARD_TOPIC:
            author_apartment_id = self._as_themselves(request.author_apartment_id)
        else:
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
            assert self.outbox is not None, "writes need the request outbox"
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
        elif post.post_type == PostType.APARTMENT_THREAD:
            self._message_posted(post, None, user.id, author_apartment_id)
        key = (user.id, author_apartment_id)
        named = post.post_type == PostType.BOARD_TOPIC
        badges = await self._badges(user.building_id, named, [key])
        return self._post_view(user, post, badges[key], comments_count=0)

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
        if post.post_type == PostType.APARTMENT_THREAD:
            author_apartment_id = self._thread_author(
                user, _thread_apartment(post), request.author_apartment_id
            )
        elif post.post_type == PostType.BOARD_TOPIC:
            author_apartment_id = self._as_themselves(request.author_apartment_id)
        else:
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
        # greatest(): a concurrent comment with a later time is never overwritten by this one.
        await self.session.execute(
            update(Post)
            .where(Post.id == post.id)
            .values(last_activity_at=func.greatest(Post.last_activity_at, comment.created_at))
        )
        if post.post_type == PostType.APARTMENT_THREAD:
            self._message_posted(post, comment.id, user.id, author_apartment_id)
        key = (user.id, author_apartment_id)
        named = post.post_type == PostType.BOARD_TOPIC
        badges = await self._badges(user.building_id, named, [key])
        return self._comment_view(user, comment, badges[key])

    async def delete_post(self, user: CurrentUserDTO, post_id: uuid.UUID) -> None:
        """Moderation: soft-delete a forum topic (the chairman) or a board topic (any board
        member) with its comments, for everyone, and record it as PostDeleted.

        One conditional UPDATE, so a concurrent second delete finds nothing and gets 404.
        """
        allowed = deletable_types(user.building_role)
        deleted = None
        if allowed:
            deleted = (
                await self.session.execute(
                    update(Post)
                    .where(
                        Post.id == post_id,
                        Post.building_id == user.building_id,
                        Post.deleted_at.is_(None),
                        Post.post_type.in_(sorted(allowed)),
                    )
                    .values(deleted_at=datetime.now(UTC), deleted_by_user_id=user.id)
                    .returning(Post.id, Post.post_type)
                )
            ).first()
        if deleted is not None:
            assert self.outbox is not None, "writes need the request outbox"
            self.outbox.add(
                PostDeleted(
                    post_id=deleted.id,
                    building_id=user.building_id,
                    post_type=deleted.post_type,
                    deleted_by_user_id=user.id,
                )
            )
            return
        post = await self._visible_post(user, post_id)  # 404 when unknown or already deleted
        if not is_deletable(post.post_type):
            raise DomainError(
                status_code=409,
                error_code="POST_NOT_DELETABLE",
                message="Only forum and board topics can be deleted; resolve an emergency instead.",
                extra={"post_type": PostType(post.post_type).value},
            )
        if post.post_type not in allowed:
            raise DomainError(
                status_code=403,
                error_code="FORBIDDEN",
                message="Only the chairman deletes forum topics.",
            )
        raise _post_not_found()  # unreachable unless the post changed meanwhile

    # --- Internals ------------------------------------------------------------------------------

    @staticmethod
    def _visible_to(user: CurrentUserDTO) -> ColumnElement[bool]:
        """Posts of the caller's building that the caller may see; one rule per post type.

        The board sees every post. Otherwise emergencies, notices and forum topics follow their
        target, threads the "since they moved in" rule (_visible_threads), and board topics
        nobody. A deleted topic is hidden from everyone, the chairman included.
        """
        in_building = and_(Post.building_id == user.building_id, Post.deleted_at.is_(None))
        if sees_all_targets(user.building_role):
            return in_building
        targeted = and_(
            Post.post_type.not_in([PostType.APARTMENT_THREAD, PostType.BOARD_TOPIC]),
            CommunicationService._targeting(user),
        )
        threads = and_(
            Post.post_type == PostType.APARTMENT_THREAD,
            CommunicationService._visible_threads(user),
        )
        return and_(in_building, or_(targeted, threads))

    @staticmethod
    def _targeting(user: CurrentUserDTO) -> ColumnElement[bool]:
        """The posts whose target includes the caller.

        ENTRANCE matches any entrance of the caller's apartments, APARTMENT_LIST any of the
        caller's apartments, so a user with two apartments sees the alerts of both.
        """
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
        return or_(*targeted)

    @staticmethod
    def _visible_threads(user: CurrentUserDTO) -> ColumnElement[bool]:
        """Threads of the caller's apartments started no earlier than their membership.

        A member who wrote in a thread from that apartment keeps it: reopening an ended
        membership moves `since` to now, and the resident must not lose the threads they took
        part in. Writing as the board does not count, so a former board member who moves in
        later does not read the previous residents' threads.
        """
        if not user.memberships:
            return false()
        return or_(
            *(
                and_(
                    Post.target_apartment_ids.contains([str(membership.apartment_id)]),
                    or_(
                        Post.created_at >= membership.since,
                        CommunicationService._wrote_from(user, membership.apartment_id),
                    ),
                )
                for membership in user.memberships
            )
        )

    @staticmethod
    def _wrote_from(user: CurrentUserDTO, apartment_id: uuid.UUID) -> ColumnElement[bool]:
        """The caller started the post or commented on it on behalf of this apartment."""
        return or_(
            and_(Post.author_user_id == user.id, Post.author_apartment_id == apartment_id),
            exists().where(
                Comment.post_id == Post.id,
                Comment.author_user_id == user.id,
                Comment.author_apartment_id == apartment_id,
            ),
        )

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
                raise _invalid_author_apartment()
            return requested
        if default_to_membership and user.memberships:
            return user.memberships[0].apartment_id
        return None

    async def _check_new_thread(
        self, user: CurrentUserDTO, request: PostCreateRequest
    ) -> uuid.UUID | None:
        """A thread goes to one apartment of the building (checked before): the board writes to
        any of them, anyone else only from their own. Returns the author's apartment."""
        (apartment_id,) = request.target_apartment_ids or ()
        if not sees_all_targets(user.building_role) and user.membership_for(apartment_id) is None:
            raise DomainError(
                status_code=403,
                error_code="FORBIDDEN",
                message="You can write to the board only from an apartment you belong to.",
            )
        if not await self.property_service.has_active_members(apartment_id):
            raise DomainError(
                status_code=422,
                error_code="APARTMENT_HAS_NO_RESIDENTS",
                message="Nobody in this apartment could read the thread.",
            )
        return self._thread_author(user, apartment_id, request.author_apartment_id)

    def _message_posted(
        self,
        thread: Post,
        comment_id: uuid.UUID | None,
        author_user_id: uuid.UUID,
        author_apartment_id: uuid.UUID | None,
    ) -> None:
        assert self.outbox is not None, "writes need the request outbox"
        self.outbox.add(
            ApartmentMessagePosted(
                post_id=thread.id,
                comment_id=comment_id,
                building_id=thread.building_id,
                apartment_id=_thread_apartment(thread),
                author_user_id=author_user_id,
                from_board=author_apartment_id is None,
            )
        )

    @staticmethod
    def _thread_author(
        user: CurrentUserDTO, thread_apartment_id: uuid.UUID, requested: uuid.UUID | None
    ) -> uuid.UUID | None:
        """The server decides who writes as whom; an explicit apartment must agree with it."""
        author_apartment_id = thread_author_apartment(thread_apartment_id, user.apartment_ids)
        if requested is not None and requested != author_apartment_id:
            raise _invalid_author_apartment()
        return author_apartment_id

    async def _badges(
        self, building_id: uuid.UUID, by_name: bool, keys: list[AuthorKey]
    ) -> dict[AuthorKey, AuthorBadgeDTO]:
        """Author badges from property: masked everywhere, by full name in the board's
        micro-forum (the documented exception, AGENTS.md), only within the caller's building."""
        if not keys:
            return {}
        if not by_name:
            return await self.property_service.get_masked_author_badges(keys)
        named = await self.property_service.get_named_author_badges(
            building_id, (user_id for user_id, _ in keys)
        )
        return {key: named[key[0]] for key in keys}

    @staticmethod
    def _as_themselves(requested: uuid.UUID | None) -> None:
        """In the board's micro-forum everyone writes as themselves, never for an apartment."""
        if requested is not None:
            raise _invalid_author_apartment()

    async def _post_views(self, user: CurrentUserDTO, posts: list[Post]) -> list[PostView]:
        if not posts:
            return []
        counts = await self._comment_counts(post.id for post in posts)
        is_named = {post.id: post.post_type == PostType.BOARD_TOPIC for post in posts}
        # Two separate lookups: the same author key (e.g. the chairman, no apartment) must never
        # carry a name from the board's micro-forum onto a notice that residents read.
        named = await self._badges(
            user.building_id, True, [_author_key(p) for p in posts if is_named[p.id]]
        )
        masked = await self._badges(
            user.building_id, False, [_author_key(p) for p in posts if not is_named[p.id]]
        )
        return [
            self._post_view(
                user,
                post,
                (named if is_named[post.id] else masked)[_author_key(post)],
                counts.get(post.id, 0),
            )
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
            last_activity_at=post.last_activity_at,
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
