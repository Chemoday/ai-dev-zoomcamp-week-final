"""Public facade of the property module for routers and other modules."""

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession
from src.core.exceptions import DomainError
from src.modules.property.models import Apartment, ApartmentMembership, User
from src.modules.property.policies import format_author_badge, most_private_display_mode
from src.modules.property.schemas import (
    ApartmentDTO,
    ApartmentMembershipDTO,
    AuthorBadgeDTO,
    BuildingRole,
    CurrentUserDTO,
    DisplayMode,
    MembershipView,
    MeResponse,
    PrivacyUpdateResponse,
    RoleBadge,
)

# Shown when a post or comment outlives its author's account.
UNKNOWN_AUTHOR_BADGE = AuthorBadgeDTO(
    display_label="Неизвестный автор", role_badge=RoleBadge.RESIDENT
)

type AuthorKey = tuple[uuid.UUID, uuid.UUID | None]

# Entrance, then apartment number in natural order: "2" < "10" < "14" < "14A".
_APARTMENT_ORDER = (Apartment.entrance, func.length(Apartment.number), Apartment.number)


class PropertyService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Authentication support -----------------------------------------------------------------

    async def get_current_user(self, user_id: uuid.UUID) -> CurrentUserDTO | None:
        """Load an active user with their memberships, or None if unknown or deactivated."""
        user = await self.session.get(User, user_id)
        if user is None or not user.is_active:
            return None
        memberships = await self._memberships_of(user.id, user.building_id)
        return CurrentUserDTO(
            id=user.id,
            building_id=user.building_id,
            building_role=user.building_role,
            privacy_display_mode=most_private_display_mode(
                m.privacy_display_mode for m in memberships
            ),
            memberships=tuple(memberships),
        )

    # --- Profile and privacy --------------------------------------------------------------------

    async def get_me(self, current_user: CurrentUserDTO) -> MeResponse:
        full_name = await self._full_name_of(current_user.id)
        return MeResponse(
            user_id=current_user.id,
            building_id=current_user.building_id,
            building_role=current_user.building_role,
            privacy_display_mode=current_user.privacy_display_mode,
            memberships=[
                MembershipView(
                    **membership.model_dump(),
                    preview_badge=self._badge_for_membership(
                        full_name, membership, current_user.building_role
                    ),
                )
                for membership in current_user.memberships
            ],
        )

    async def update_privacy_mode(
        self, current_user: CurrentUserDTO, membership_id: uuid.UUID, mode: DisplayMode
    ) -> PrivacyUpdateResponse:
        membership = await self.session.scalar(
            select(ApartmentMembership).where(
                ApartmentMembership.id == membership_id,
                ApartmentMembership.user_id == current_user.id,
            )
        )
        if membership is None:
            # Same answer for "does not exist" and "not yours", so IDs cannot be probed.
            raise DomainError(
                status_code=404,
                error_code="MEMBERSHIP_NOT_FOUND",
                message="Membership not found.",
            )
        membership.privacy_display_mode = mode
        await self.session.flush()

        apartment = await self.session.get(Apartment, membership.apartment_id)
        full_name = await self._full_name_of(current_user.id)
        badge = format_author_badge(
            full_name=full_name,
            apt_number=apartment.number if apartment else None,
            relation_type=membership.relation_type,
            building_role=current_user.building_role,
            display_mode=mode,
        )
        return PrivacyUpdateResponse(
            membership_id=membership.id, privacy_display_mode=mode, preview_badge=badge
        )

    # --- Building structure for other modules --------------------------------------------------

    async def list_apartments(self, building_id: uuid.UUID) -> list[ApartmentDTO]:
        """All apartments of a building, by entrance and natural apartment number."""
        apartments = await self.session.scalars(
            select(Apartment)
            .where(Apartment.building_id == building_id)
            .order_by(*_APARTMENT_ORDER)
        )
        return [
            ApartmentDTO(id=a.id, number=a.number, entrance=a.entrance, floor=a.floor)
            for a in apartments
        ]

    # --- Privacy masker for other modules -------------------------------------------------------

    async def get_masked_author_badge(
        self, user_id: uuid.UUID, apartment_id: uuid.UUID | None
    ) -> AuthorBadgeDTO:
        badges = await self.get_masked_author_badges([(user_id, apartment_id)])
        return badges[(user_id, apartment_id)]

    async def get_masked_author_badges(
        self, authors: Iterable[AuthorKey]
    ) -> dict[AuthorKey, AuthorBadgeDTO]:
        """Resolve many (user_id, apartment_id) authors at once (no N+1 queries for feeds).

        The display mode comes from the user's membership in that apartment. Without an
        apartment context the most private of the user's modes applies, so consenting to be named
        in one apartment never reveals the name elsewhere. If the user no longer lives in the
        apartment, only the apartment is shown, because nobody consented to show the name there.
        """
        keys = set(authors)
        if not keys:
            return {}
        user_ids = {user_id for user_id, _ in keys}

        users = {
            user.id: user
            for user in await self.session.scalars(select(User).where(User.id.in_(user_ids)))
        }
        membership_rows = (
            await self.session.execute(
                select(ApartmentMembership, Apartment.number)
                .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
                .join(User, User.id == ApartmentMembership.user_id)
                .where(
                    ApartmentMembership.user_id.in_(user_ids),
                    Apartment.building_id == User.building_id,
                )
            )
        ).all()
        memberships: dict[tuple[uuid.UUID, uuid.UUID], tuple[ApartmentMembership, str]] = {}
        modes_by_user: dict[uuid.UUID, list[DisplayMode]] = {}
        for membership, number in membership_rows:
            memberships[(membership.user_id, membership.apartment_id)] = (membership, number)
            modes_by_user.setdefault(membership.user_id, []).append(membership.privacy_display_mode)

        orphan_apartment_ids = {
            apartment_id
            for user_id, apartment_id in keys
            if apartment_id is not None and (user_id, apartment_id) not in memberships
        }
        orphan_numbers: dict[uuid.UUID, str] = {}
        if orphan_apartment_ids:
            orphan_numbers = dict(
                (
                    await self.session.execute(
                        select(Apartment.id, Apartment.number).where(
                            Apartment.id.in_(orphan_apartment_ids)
                        )
                    )
                ).all()
            )

        result: dict[AuthorKey, AuthorBadgeDTO] = {}
        for user_id, apartment_id in keys:
            user = users.get(user_id)
            if user is None:
                result[(user_id, apartment_id)] = UNKNOWN_AUTHOR_BADGE
                continue
            if apartment_id is None:
                badge = format_author_badge(
                    full_name=user.full_name,
                    apt_number=None,
                    relation_type=None,
                    building_role=user.building_role,
                    display_mode=most_private_display_mode(modes_by_user.get(user_id, ())),
                )
            elif (user_id, apartment_id) in memberships:
                membership, number = memberships[(user_id, apartment_id)]
                badge = format_author_badge(
                    full_name=user.full_name,
                    apt_number=number,
                    relation_type=membership.relation_type,
                    building_role=user.building_role,
                    display_mode=membership.privacy_display_mode,
                )
            else:
                badge = format_author_badge(
                    full_name=user.full_name,
                    apt_number=orphan_numbers.get(apartment_id),
                    relation_type=None,
                    building_role=user.building_role,
                    display_mode=DisplayMode.APARTMENT_ONLY,
                )
            result[(user_id, apartment_id)] = badge
        return result

    # --- Internals ------------------------------------------------------------------------------

    async def _memberships_of(
        self, user_id: uuid.UUID, building_id: uuid.UUID
    ) -> list[ApartmentMembershipDTO]:
        # Only apartments of the user's own building: CurrentUserDTO.building_id and the
        # membership apartments must never disagree for the modules that scope by them.
        rows = await self.session.execute(
            select(ApartmentMembership, Apartment)
            .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
            .where(ApartmentMembership.user_id == user_id, Apartment.building_id == building_id)
            .order_by(*_APARTMENT_ORDER)
        )
        return [
            ApartmentMembershipDTO(
                membership_id=membership.id,
                apartment_id=apartment.id,
                apartment_number=apartment.number,
                entrance=apartment.entrance,
                floor=apartment.floor,
                relation_type=membership.relation_type,
                can_submit_readings=membership.can_submit_readings,
                can_view_bills=membership.can_view_bills,
                can_vote=membership.can_vote,
                privacy_display_mode=membership.privacy_display_mode,
            )
            for membership, apartment in rows.all()
        ]

    async def _full_name_of(self, user_id: uuid.UUID) -> str:
        full_name = await self.session.scalar(select(User.full_name).where(User.id == user_id))
        return full_name or ""

    @staticmethod
    def _badge_for_membership(
        full_name: str, membership: ApartmentMembershipDTO, building_role: BuildingRole
    ) -> AuthorBadgeDTO:
        return format_author_badge(
            full_name=full_name,
            apt_number=membership.apartment_number,
            relation_type=membership.relation_type,
            building_role=building_role,
            display_mode=membership.privacy_display_mode,
        )


def get_property_service(session: DbSession) -> PropertyService:
    return PropertyService(session)
