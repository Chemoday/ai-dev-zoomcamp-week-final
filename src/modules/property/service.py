"""Public facade of the property module for routers and other modules."""

import uuid
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession
from src.core.exceptions import DomainError
from src.modules.property.models import Apartment, ApartmentMembership, Building, User
from src.modules.property.policies import (
    apartment_in_target,
    format_author_badge,
    most_private_display_mode,
)
from src.modules.property.schemas import (
    ApartmentDTO,
    ApartmentMembershipDTO,
    AuthorBadgeDTO,
    BuildingResponse,
    BuildingRole,
    CurrentUserDTO,
    DeliveryContactDTO,
    DisplayMode,
    Locale,
    LocaleUpdate,
    MembershipView,
    MeResponse,
    PrivacyUpdateResponse,
    RecipientDTO,
    RoleBadge,
    TargetScope,
)

# Shown when a post or comment outlives its author's account.
UNKNOWN_AUTHOR_BADGE = AuthorBadgeDTO(display_label="Unknown author", role_badge=RoleBadge.RESIDENT)

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
        locale = await self.session.scalar(select(User.locale).where(User.id == current_user.id))
        return MeResponse(
            user_id=current_user.id,
            building_id=current_user.building_id,
            building_role=current_user.building_role,
            privacy_display_mode=current_user.privacy_display_mode,
            locale=locale,
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

    async def update_locale(
        self, current_user: CurrentUserDTO, locale: Locale | None
    ) -> LocaleUpdate:
        """Language of the caller's notifications sent outside the app (None = instance default)."""
        await self.session.execute(
            update(User).where(User.id == current_user.id).values(locale=locale)
        )
        return LocaleUpdate(locale=locale)

    # --- Notification recipients (system-level, used by the notifications module) ---------------

    async def resolve_recipients(
        self,
        building_id: uuid.UUID,
        scope: TargetScope,
        entrance: int | None,
        apartment_ids: Iterable[uuid.UUID],
    ) -> list[RecipientDTO]:
        """Active users with a membership in a targeted apartment of the building, once each.

        Staff without an apartment are not reached through targeting (docs/ARCHITECTURE.md 3.4);
        the caller adds them by role (`users_with_roles`).
        """
        targeted = frozenset(apartment_ids)
        rows = await self.session.execute(
            select(User.id, User.locale, Apartment.id, Apartment.entrance)
            .join(ApartmentMembership, ApartmentMembership.user_id == User.id)
            .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
            .where(
                User.building_id == building_id,
                User.is_active.is_(True),
                Apartment.building_id == building_id,
            )
        )
        recipients: dict[uuid.UUID, RecipientDTO] = {}
        for user_id, locale, apartment_id, apartment_entrance in rows.all():
            if user_id not in recipients and apartment_in_target(
                scope, entrance, targeted, apartment_id=apartment_id, entrance=apartment_entrance
            ):
                recipients[user_id] = RecipientDTO(user_id=user_id, locale=locale)
        return sorted(recipients.values(), key=lambda r: str(r.user_id))

    async def users_with_roles(
        self, building_id: uuid.UUID, roles: Iterable[BuildingRole]
    ) -> list[RecipientDTO]:
        """Active users of the building with one of the building roles."""
        rows = await self.session.execute(
            select(User.id, User.locale)
            .where(
                User.building_id == building_id,
                User.is_active.is_(True),
                User.building_role.in_(list(roles)),
            )
            .order_by(User.id)
        )
        return [RecipientDTO(user_id=user_id, locale=locale) for user_id, locale in rows.all()]

    async def has_delivery_email(self, user_id: uuid.UUID) -> bool:
        """Whether the user can be reached by e-mail (the address itself never leaves)."""
        email = await self.session.scalar(select(User.email).where(User.id == user_id))
        return bool(email)

    async def with_delivery_email(self, user_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these users have an e-mail address (one query; the addresses stay here)."""
        ids = list(user_ids)
        if not ids:
            return set()
        rows = await self.session.scalars(
            select(User.id).where(User.id.in_(ids), User.email.is_not(None), User.email != "")
        )
        return set(rows)

    async def get_delivery_contact(self, user_id: uuid.UUID) -> DeliveryContactDTO | None:
        """The address to send one message to, or None (no address, or the user is inactive).

        The documented PII exception (docs/ARCHITECTURE.md 3.4): the caller keeps it in memory
        for one send and never logs or stores it.
        """
        email = await self.session.scalar(
            select(User.email).where(User.id == user_id, User.is_active.is_(True))
        )
        return DeliveryContactDTO(email=email) if email else None

    async def get_recipient(self, user_id: uuid.UUID) -> RecipientDTO | None:
        """An active user as a notification recipient (id and language), or None."""
        row = await self.session.execute(
            select(User.id, User.locale).where(User.id == user_id, User.is_active.is_(True))
        )
        found = row.first()
        return RecipientDTO(user_id=found.id, locale=found.locale) if found else None

    async def reading_submitters(self, apartment_ids: Iterable[uuid.UUID]) -> list[RecipientDTO]:
        """Active members of these apartments who may submit readings, once each."""
        ids = list(apartment_ids)
        if not ids:
            return []
        rows = await self.session.execute(
            select(User.id, User.locale)
            .join(ApartmentMembership, ApartmentMembership.user_id == User.id)
            .where(
                ApartmentMembership.apartment_id.in_(ids),
                ApartmentMembership.can_submit_readings.is_(True),
                User.is_active.is_(True),
            )
            .distinct()
            .order_by(User.id)
        )
        return [RecipientDTO(user_id=user_id, locale=locale) for user_id, locale in rows.all()]

    async def is_user_active(self, user_id: uuid.UUID) -> bool:
        active = await self.session.scalar(select(User.is_active).where(User.id == user_id))
        return bool(active)

    # --- Building details -----------------------------------------------------------------------

    async def get_building(self, current_user: CurrentUserDTO) -> BuildingResponse:
        building = await self.session.get_one(Building, current_user.building_id)
        apartment_count, entrance_count, area = (
            await self.session.execute(
                select(
                    func.count(Apartment.id),
                    func.count(Apartment.entrance.distinct()),
                    func.coalesce(func.sum(Apartment.area_m2), 0),
                ).where(Apartment.building_id == building.id)
            )
        ).one()
        return BuildingResponse(
            building_id=building.id,
            name=building.name,
            registry_code=building.registry_code,
            address=building.address,
            country_code=building.country_code,
            reading_deadline_day=building.reading_deadline_day,
            apartment_count=apartment_count,
            entrance_count=entrance_count,
            heated_area_m2=Decimal(area),
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
