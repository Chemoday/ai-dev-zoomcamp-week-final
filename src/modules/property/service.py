"""Public facade of the property module for routers and other modules."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession
from src.core.exceptions import DomainError
from src.core.validation import canonical_email
from src.modules.property.models import (
    Apartment,
    ApartmentMembership,
    Building,
    User,
)
from src.modules.property.policies import (
    apartment_in_target,
    format_author_badge,
    format_named_badge,
    most_private_display_mode,
)
from src.modules.property.schemas import (
    ApartmentCard,
    ApartmentDTO,
    ApartmentListResponse,
    ApartmentMembershipDTO,
    ApartmentRefDTO,
    ApartmentSummary,
    AuthorBadgeDTO,
    BuildingResponse,
    BuildingRole,
    CardResident,
    CurrentUserDTO,
    DeliveryContactDTO,
    DisplayMode,
    ElectorDTO,
    Locale,
    LocaleUpdate,
    MembershipRefDTO,
    MembershipView,
    MeResponse,
    PersonDTO,
    PersonMembershipDTO,
    PrivacyUpdateResponse,
    RecipientDTO,
    RelationType,
    ResidentDTO,
    RoleBadge,
    TargetScope,
    UserRefDTO,
)

# The name of a closed account (its personal data is removed, docs/API.md 7).
FORMER_RESIDENT_NAME = "Former resident"
_RELATION_ORDER = (RelationType.OWNER, RelationType.TENANT, RelationType.RESIDENT)
_ROLE_ORDER = (
    BuildingRole.CHAIRMAN,
    BuildingRole.BOARD,
    BuildingRole.ACCOUNTANT,
    BuildingRole.USER,
)


class LastChairmanError(DomainError):
    """The change would leave the building without an active chairman."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            error_code="LAST_CHAIRMAN",
            message="The building needs an active chairman; make someone else chairman first.",
        )


# Shown when a post or comment outlives its author's account.
UNKNOWN_AUTHOR_BADGE = AuthorBadgeDTO(display_label="Unknown author", role_badge=RoleBadge.RESIDENT)

type AuthorKey = tuple[uuid.UUID, uuid.UUID | None]

# Entrance, then apartment number in natural order: "2" < "10" < "14" < "14A".
_APARTMENT_ORDER = (Apartment.entrance, func.length(Apartment.number), Apartment.number)
# Ended memberships are history: every query that resolves people filters them
# (docs/ARCHITECTURE.md 4.3).
ACTIVE_MEMBERSHIP = ApartmentMembership.ended_at.is_(None)


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
                ACTIVE_MEMBERSHIP,
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
                ACTIVE_MEMBERSHIP,
            )
        )
        recipients: dict[uuid.UUID, RecipientDTO] = {}
        for user_id, locale, apartment_id, apartment_entrance in rows.all():
            if user_id not in recipients and apartment_in_target(
                scope, entrance, targeted, apartment_id=apartment_id, entrance=apartment_entrance
            ):
                recipients[user_id] = RecipientDTO(user_id=user_id, locale=locale)
        return sorted(recipients.values(), key=lambda r: str(r.user_id))

    async def poll_electors(
        self,
        building_id: uuid.UUID,
        scope: TargetScope,
        entrance: int | None,
        apartment_ids: Iterable[uuid.UUID],
        *,
        owners_only: bool,
    ) -> list[ElectorDTO]:
        """Active users with an active membership (an OWNER one when `owners_only`) in a
        targeted apartment of the building: one row per user and apartment (epic E3).

        The shared targeting rule decides which apartments are targeted, as for alerts.
        """
        targeted = frozenset(apartment_ids)
        statement = (
            select(User.id, Apartment.id, Apartment.entrance)
            .join(ApartmentMembership, ApartmentMembership.user_id == User.id)
            .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
            .where(
                User.building_id == building_id,
                User.is_active.is_(True),
                Apartment.building_id == building_id,
                ACTIVE_MEMBERSHIP,
            )
        )
        if owners_only:
            statement = statement.where(ApartmentMembership.relation_type == RelationType.OWNER)
        electors = {
            (user_id, apartment_id)
            for user_id, apartment_id, apartment_entrance in (await self.session.execute(statement))
            if apartment_in_target(
                scope, entrance, targeted, apartment_id=apartment_id, entrance=apartment_entrance
            )
        }
        return [
            ElectorDTO(user_id=user_id, apartment_id=apartment_id)
            for user_id, apartment_id in sorted(
                electors, key=lambda pair: (str(pair[1]), str(pair[0]))
            )
        ]

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
        email = await self.session.scalar(
            select(User.email).where(User.id == user_id, User.is_active.is_(True))
        )
        return bool(email)

    async def with_delivery_email(self, user_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these users have an e-mail address (one query; the addresses stay here)."""
        ids = list(user_ids)
        if not ids:
            return set()
        rows = await self.session.scalars(
            select(User.id).where(
                User.id.in_(ids),
                User.is_active.is_(True),
                User.email.is_not(None),
                User.email != "",
            )
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
                ACTIVE_MEMBERSHIP,
                User.is_active.is_(True),
            )
            .distinct()
            .order_by(User.id)
        )
        return [RecipientDTO(user_id=user_id, locale=locale) for user_id, locale in rows.all()]

    async def find_active_user_by_email(self, email: str) -> UserRefDTO | None:
        """The active user with this canonical e-mail (module auth: sign-in links), or None."""
        row = (
            await self.session.execute(
                select(User.id, User.building_id, User.building_role, User.locale).where(
                    User.email == canonical_email(email), User.is_active.is_(True)
                )
            )
        ).first()
        if row is None:
            return None
        return UserRefDTO(
            user_id=row.id,
            building_id=row.building_id,
            building_role=row.building_role,
            locale=row.locale,
        )

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

    # --- Apartment cards for the board (epic E8) ------------------------------------------------
    # The router allows only CARD_VIEWER_ROLES; residents appear as relations, never as people.

    async def list_apartment_cards(self, current_user: CurrentUserDTO) -> ApartmentListResponse:
        apartments = list(
            await self.session.scalars(
                select(Apartment)
                .where(Apartment.building_id == current_user.building_id)
                .order_by(*_APARTMENT_ORDER)
            )
        )
        members = await self._card_residents([apartment.id for apartment in apartments])
        return ApartmentListResponse(
            apartments=[
                _apartment_summary(apartment, members.get(apartment.id, []))
                for apartment in apartments
            ]
        )

    async def get_apartment_card(
        self, current_user: CurrentUserDTO, apartment_id: uuid.UUID
    ) -> ApartmentCard:
        apartment = await self.session.scalar(
            select(Apartment).where(
                Apartment.id == apartment_id, Apartment.building_id == current_user.building_id
            )
        )
        if apartment is None:
            raise DomainError(
                status_code=404, error_code="APARTMENT_NOT_FOUND", message="No such apartment."
            )
        residents = (await self._card_residents([apartment.id])).get(apartment.id, [])
        summary = _apartment_summary(apartment, residents)
        return ApartmentCard(**summary.model_dump(), residents=residents)

    async def _card_residents(
        self, apartment_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[CardResident]]:
        """Active memberships of active accounts per apartment: owners, tenants, then family,
        each by start (the same people GET /property/me and the residents list count)."""
        if not apartment_ids:
            return {}
        rows = (
            await self.session.execute(
                select(
                    ApartmentMembership.apartment_id,
                    ApartmentMembership.relation_type,
                    ApartmentMembership.started_at,
                )
                .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
                .join(User, User.id == ApartmentMembership.user_id)
                .where(
                    ApartmentMembership.apartment_id.in_(apartment_ids),
                    ACTIVE_MEMBERSHIP,
                    User.is_active.is_(True),
                    Apartment.building_id == User.building_id,
                )
                .order_by(ApartmentMembership.started_at, ApartmentMembership.id)
            )
        ).all()
        result: dict[uuid.UUID, list[CardResident]] = {}
        for apartment_id, relation_type, started_at in rows:
            result.setdefault(apartment_id, []).append(
                CardResident(relation_type=relation_type, since=started_at)
            )
        for residents in result.values():
            # Stable sort: within a relation the order by start (then id) stays.
            residents.sort(key=lambda resident: _RELATION_ORDER.index(resident.relation_type))
        return result

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

    async def get_named_author_badges(
        self, building_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, AuthorBadgeDTO]:
        """Authors of board topics by full name (epic E8, the documented exception to masking).

        Only communication's BOARD_TOPIC posts and comments use it; a closed account shows
        "Former resident", which closing writes into the name. Names of other buildings are
        never returned.
        """
        ids = set(user_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(User.id, User.full_name, User.building_role).where(
                User.id.in_(ids), User.building_id == building_id
            )
        )
        return {
            user_id: format_named_badge(full_name, role) for user_id, full_name, role in rows.all()
        }

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
                    ACTIVE_MEMBERSHIP,
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

    # --- Accounts and memberships for module auth (docs/ARCHITECTURE.md 4.2) --------------------
    # System-level: no CurrentUserDTO; module auth checks who may call them.

    async def building_ids(self) -> list[uuid.UUID]:
        """Every building of the installation (one per installation today; the operator
        command of the first chairman needs it)."""
        return list(await self.session.scalars(select(Building.id).order_by(Building.id)))

    async def user_ref(self, user_id: uuid.UUID) -> UserRefDTO | None:
        """An active user, or None (closed or unknown)."""
        row = (
            await self.session.execute(
                select(User.id, User.building_id, User.building_role, User.locale).where(
                    User.id == user_id, User.is_active.is_(True)
                )
            )
        ).first()
        if row is None:
            return None
        return UserRefDTO(
            user_id=row.id,
            building_id=row.building_id,
            building_role=row.building_role,
            locale=row.locale,
        )

    async def active_users_by_emails(self, emails: Iterable[str]) -> dict[str, uuid.UUID]:
        """Which of these canonical e-mails belong to active users (the addresses come from the
        caller; none leaves this module)."""
        wanted = {canonical_email(email) for email in emails}
        if not wanted:
            return {}
        rows = await self.session.execute(
            select(User.email, User.id).where(User.email.in_(wanted), User.is_active.is_(True))
        )
        return {email: user_id for email, user_id in rows.all() if email}

    async def create_user(
        self,
        building_id: uuid.UUID,
        *,
        email: str,
        full_name: str,
        phone: str | None,
        locale: Locale | None,
        building_role: BuildingRole,
    ) -> uuid.UUID:
        """A new account (registration after an invitation); returns its id."""
        user = User(
            building_id=building_id,
            email=canonical_email(email),
            full_name=full_name,
            phone=phone,
            locale=locale,
            building_role=building_role,
        )
        self.session.add(user)
        await self.session.flush()
        return user.id

    async def open_membership(
        self,
        user_id: uuid.UUID,
        apartment_id: uuid.UUID,
        relation_type: RelationType,
        privacy_display_mode: DisplayMode,
    ) -> uuid.UUID | None:
        """Create the membership, or reopen an ended one (a new start, the given relation and
        mode). Returns its id, or None without any change when an active one already exists."""
        await self._lock_person(user_id)
        existing = await self.session.scalar(
            select(ApartmentMembership)
            .where(
                ApartmentMembership.user_id == user_id,
                ApartmentMembership.apartment_id == apartment_id,
            )
            .with_for_update()
        )
        if existing is not None and existing.ended_at is None:
            return None
        now = datetime.now(UTC)
        values = {
            "relation_type": relation_type,
            "privacy_display_mode": privacy_display_mode,
            "can_vote": relation_type == RelationType.OWNER,
            "can_submit_readings": True,
            "can_view_bills": True,
            "started_at": now,
            "ended_at": None,
        }
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            await self.session.flush()
            return existing.id
        membership_id = await self.session.scalar(
            pg_insert(ApartmentMembership)
            .values(id=uuid.uuid4(), user_id=user_id, apartment_id=apartment_id, **values)
            .on_conflict_do_nothing(index_elements=["apartment_id", "user_id"])
            .returning(ApartmentMembership.id)
        )
        return membership_id  # None: created meanwhile by a concurrent request

    async def lock_people(self, building_id: uuid.UUID) -> None:
        """The building's people lock, held until the transaction ends (re-entrant). Every change
        of people takes it first, and module auth takes it before it locks invitation rows, so
        all paths lock in one order: this lock, an apartment, an invitation, then its tokens and
        other single rows."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"property:people:{building_id}"},
        )

    async def raise_to_owner(self, membership_id: uuid.UUID) -> bool:
        """Turn an active TENANT or RESIDENT membership into OWNER (a tenant who bought the flat);
        the privacy mode stays. Returns whether anything changed."""
        user_id = await self.session.scalar(
            select(ApartmentMembership.user_id).where(ApartmentMembership.id == membership_id)
        )
        if user_id is None:
            return False
        await self._lock_person(user_id)
        membership = await self.session.get(
            ApartmentMembership, membership_id, with_for_update=True, populate_existing=True
        )
        if (
            membership is None
            or membership.ended_at is not None
            or membership.relation_type == RelationType.OWNER
        ):
            return False
        membership.relation_type = RelationType.OWNER
        membership.can_vote = True
        await self.session.flush()
        return True

    async def membership_ref(self, membership_id: uuid.UUID) -> MembershipRefDTO | None:
        """An active membership of an active user, or None."""
        return await self._membership_ref(ApartmentMembership.id == membership_id)

    async def active_membership(
        self, user_id: uuid.UUID, apartment_id: uuid.UUID
    ) -> MembershipRefDTO | None:
        return await self._membership_ref(
            ApartmentMembership.user_id == user_id,
            ApartmentMembership.apartment_id == apartment_id,
        )

    async def end_membership(self, membership_id: uuid.UUID) -> bool:
        """End a membership; returns whether the user still has an active membership or a
        staff role (if not, module auth closes the account)."""
        user_id = await self.session.scalar(
            select(ApartmentMembership.user_id).where(ApartmentMembership.id == membership_id)
        )
        if user_id is None:
            return False
        await self._lock_person(user_id)
        membership = await self.session.get(
            ApartmentMembership, membership_id, with_for_update=True, populate_existing=True
        )
        if membership is None:
            return False
        if membership.ended_at is None:
            membership.ended_at = datetime.now(UTC)
            await self.session.flush()
        role = await self.session.scalar(
            select(User.building_role).where(User.id == membership.user_id)
        )
        others = await self.session.scalar(
            select(func.count(ApartmentMembership.id)).where(
                ApartmentMembership.user_id == membership.user_id, ACTIVE_MEMBERSHIP
            )
        )
        return bool(others) or (role is not None and role != BuildingRole.USER)

    async def set_building_role(self, user_id: uuid.UUID, role: BuildingRole) -> None:
        """Change a building role; never leaves the building without an active chairman."""
        user = await self._lock_person(user_id)
        if user is None or not user.is_active:
            return
        if user.building_role == BuildingRole.CHAIRMAN and role != BuildingRole.CHAIRMAN:
            await self._ensure_another_chairman(user)
        user.building_role = role
        await self.session.flush()

    async def close_account(self, user_id: uuid.UUID) -> bool:
        """End all memberships, deactivate the account and remove its personal data (GDPR).

        Readings keep their apartment and forum posts keep a masked badge. Module auth deletes the
        identities (passwords) in the same transaction. Returns False when the account was already
        closed or is unknown. Refuses the last active chairman (LAST_CHAIRMAN).
        """
        user = await self._lock_person(user_id)
        if user is None or not user.is_active:
            return False
        if user.building_role == BuildingRole.CHAIRMAN:
            await self._ensure_another_chairman(user)
        now = datetime.now(UTC)
        await self.session.execute(
            update(ApartmentMembership)
            .where(ApartmentMembership.user_id == user_id, ACTIVE_MEMBERSHIP)
            .values(ended_at=now)
            .execution_options(synchronize_session=False)
        )
        user.is_active = False
        user.email = None
        user.phone = None
        user.full_name = FORMER_RESIDENT_NAME
        await self.session.flush()
        return True

    async def apartment_by_number(
        self, building_id: uuid.UUID, number: str
    ) -> ApartmentRefDTO | None:
        apartment = await self.session.scalar(
            select(Apartment).where(
                Apartment.building_id == building_id, Apartment.number == number
            )
        )
        return self._apartment_ref(apartment)

    async def apartment_ref(self, apartment_id: uuid.UUID) -> ApartmentRefDTO | None:
        return self._apartment_ref(await self.session.get(Apartment, apartment_id))

    async def lock_apartment(self, apartment_id: uuid.UUID) -> None:
        """Hold the apartment's row for this transaction (the tenant limit under concurrency)."""
        await self.session.execute(
            select(Apartment.id).where(Apartment.id == apartment_id).with_for_update()
        )

    async def is_owner_of(self, user_id: uuid.UUID, apartment_id: uuid.UUID) -> bool:
        found = await self.session.scalar(
            select(ApartmentMembership.id)
            .join(User, User.id == ApartmentMembership.user_id)
            .where(
                ApartmentMembership.user_id == user_id,
                ApartmentMembership.apartment_id == apartment_id,
                ApartmentMembership.relation_type == RelationType.OWNER,
                ACTIVE_MEMBERSHIP,
                User.is_active.is_(True),
            )
        )
        return found is not None

    async def has_active_owner(self, apartment_id: uuid.UUID) -> bool:
        return await self._count_members(apartment_id, RelationType.OWNER) > 0

    async def has_active_members(self, apartment_id: uuid.UUID) -> bool:
        """Whether anyone (an active account) is an active member of the apartment: a thread
        with the board needs a reader besides the board (E8/P2)."""
        return await self._count_members(apartment_id, None) > 0

    async def active_tenant_count(self, apartment_id: uuid.UUID) -> int:
        return await self._count_members(apartment_id, RelationType.TENANT)

    async def active_chairman_count(self, building_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count(User.id)).where(
                User.building_id == building_id,
                User.building_role == BuildingRole.CHAIRMAN,
                User.is_active.is_(True),
            )
        )
        return int(count or 0)

    async def apartment_residents(self, apartment_id: uuid.UUID) -> list[ResidentDTO]:
        """The active members of one apartment, by relation, start and id, under the badge
        rules of that apartment."""
        rows = await self.session.execute(
            select(ApartmentMembership, Apartment.number, User.full_name, User.building_role)
            .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
            .join(User, User.id == ApartmentMembership.user_id)
            .where(
                ApartmentMembership.apartment_id == apartment_id,
                ACTIVE_MEMBERSHIP,
                User.is_active.is_(True),
                Apartment.building_id == User.building_id,
            )
        )
        residents = [
            ResidentDTO(
                user_id=membership.user_id,
                membership_id=membership.id,
                relation_type=membership.relation_type,
                display_label=format_author_badge(
                    full_name=full_name,
                    apt_number=number,
                    relation_type=membership.relation_type,
                    building_role=role,
                    display_mode=membership.privacy_display_mode,
                ).display_label,
                since=membership.started_at,
            )
            for membership, number, full_name, role in rows.all()
        ]
        return sorted(
            residents,
            key=lambda r: (_RELATION_ORDER.index(r.relation_type), r.since, str(r.user_id)),
        )

    async def building_people(self, building_id: uuid.UUID) -> list[PersonDTO]:
        """Every active person of the building with their active memberships, under the badge
        rules, in the order of docs/API.md 7 (`GET /auth/users`)."""
        users = list(
            await self.session.scalars(
                select(User).where(User.building_id == building_id, User.is_active.is_(True))
            )
        )
        rows = await self.session.execute(
            select(ApartmentMembership, Apartment)
            .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
            .where(
                ApartmentMembership.user_id.in_([u.id for u in users]),
                Apartment.building_id == building_id,
                ACTIVE_MEMBERSHIP,
            )
        )
        by_user: dict[uuid.UUID, list[tuple[ApartmentMembership, Apartment]]] = {}
        for membership, apartment in rows.all():
            by_user.setdefault(membership.user_id, []).append((membership, apartment))

        def membership_key(item: tuple[ApartmentMembership, Apartment]) -> tuple:
            membership, apartment = item
            return (
                apartment.entrance,
                len(apartment.number),
                apartment.number,
                _RELATION_ORDER.index(membership.relation_type),
                membership.started_at,
                str(membership.user_id),
            )

        people: list[tuple[tuple, PersonDTO]] = []
        for user in users:
            memberships = sorted(by_user.get(user.id, []), key=membership_key)
            person = PersonDTO(
                user_id=user.id,
                building_role=user.building_role,
                display_label=format_author_badge(
                    full_name=user.full_name,
                    apt_number=None,
                    relation_type=None,
                    building_role=user.building_role,
                    display_mode=most_private_display_mode(
                        m.privacy_display_mode for m, _ in memberships
                    ),
                ).display_label,
                memberships=tuple(
                    PersonMembershipDTO(
                        membership_id=m.id,
                        apartment_id=a.id,
                        apartment_number=a.number,
                        relation_type=m.relation_type,
                        display_label=format_author_badge(
                            full_name=user.full_name,
                            apt_number=a.number,
                            relation_type=m.relation_type,
                            building_role=user.building_role,
                            display_mode=m.privacy_display_mode,
                        ).display_label,
                        since=m.started_at,
                    )
                    for m, a in memberships
                ),
            )
            # Accounts without a membership first (by role), then by the first membership.
            key = (
                (1, *membership_key(memberships[0]))
                if memberships
                else (0, _ROLE_ORDER.index(user.building_role), str(user.id))
            )
            people.append((key, person))
        return [person for _, person in sorted(people, key=lambda item: item[0])]

    async def membership_end_times(
        self, membership_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, datetime | None]:
        ids = list(membership_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(ApartmentMembership.id, ApartmentMembership.ended_at).where(
                ApartmentMembership.id.in_(ids)
            )
        )
        return {membership_id: ended for membership_id, ended in rows.all()}

    async def building_roles(
        self, user_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, BuildingRole | None]:
        """The current role of each user; None for a closed or unknown one."""
        ids = list(user_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(User.id, User.building_role).where(User.id.in_(ids), User.is_active.is_(True))
        )
        found = {user_id: role for user_id, role in rows.all()}
        return {user_id: found.get(user_id) for user_id in ids}

    async def inactive_user_ids(self, user_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Which of these users are closed or unknown (the notifications backstop)."""
        ids = set(user_ids)
        if not ids:
            return set()
        active = await self.session.scalars(
            select(User.id).where(User.id.in_(ids), User.is_active.is_(True))
        )
        return ids - set(active)

    async def _membership_ref(self, *conditions: Any) -> MembershipRefDTO | None:
        row = (
            await self.session.execute(
                select(ApartmentMembership, Apartment.building_id)
                .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
                .join(User, User.id == ApartmentMembership.user_id)
                .where(*conditions, ACTIVE_MEMBERSHIP, User.is_active.is_(True))
            )
        ).first()
        if row is None:
            return None
        membership, building_id = row
        return MembershipRefDTO(
            membership_id=membership.id,
            user_id=membership.user_id,
            apartment_id=membership.apartment_id,
            building_id=building_id,
            relation_type=membership.relation_type,
        )

    async def _count_members(self, apartment_id: uuid.UUID, relation: RelationType | None) -> int:
        """Active members of active accounts; `relation` None counts every relation."""
        statement = (
            select(func.count(ApartmentMembership.id))
            .join(User, User.id == ApartmentMembership.user_id)
            .where(
                ApartmentMembership.apartment_id == apartment_id,
                ACTIVE_MEMBERSHIP,
                User.is_active.is_(True),
            )
        )
        if relation is not None:
            statement = statement.where(ApartmentMembership.relation_type == relation)
        return int(await self.session.scalar(statement) or 0)

    async def _lock_person(self, user_id: uuid.UUID) -> User | None:
        """Serialise every change of people in one building, then lock the person's row.

        The order is always the same (the building's lock, then the user's row), so two step-downs
        of two chairmen, or ending a membership while another one opens, never deadlock, and the
        count of a person's memberships or of the chairmen cannot change underneath a decision.
        """
        building_id = await self.session.scalar(select(User.building_id).where(User.id == user_id))
        if building_id is None:
            return None
        await self.lock_people(building_id)
        return await self.session.get(User, user_id, with_for_update=True, populate_existing=True)

    async def _ensure_another_chairman(self, user: User) -> None:
        # Called under _lock_person, so the building's people do not change meanwhile; the rows
        # are locked as well for anything that changes a role without it.
        chairmen = list(
            await self.session.scalars(
                select(User.id)
                .where(
                    User.building_id == user.building_id,
                    User.building_role == BuildingRole.CHAIRMAN,
                    User.is_active.is_(True),
                )
                .order_by(User.id)
                .with_for_update()
            )
        )
        if not any(chairman != user.id for chairman in chairmen):
            raise LastChairmanError()

    @staticmethod
    def _apartment_ref(apartment: Apartment | None) -> ApartmentRefDTO | None:
        if apartment is None:
            return None
        return ApartmentRefDTO(
            apartment_id=apartment.id, building_id=apartment.building_id, number=apartment.number
        )

    # --- Internals ------------------------------------------------------------------------------

    async def _memberships_of(
        self, user_id: uuid.UUID, building_id: uuid.UUID
    ) -> list[ApartmentMembershipDTO]:
        # Only apartments of the user's own building: CurrentUserDTO.building_id and the
        # membership apartments must never disagree for the modules that scope by them.
        rows = await self.session.execute(
            select(ApartmentMembership, Apartment)
            .join(Apartment, Apartment.id == ApartmentMembership.apartment_id)
            .where(
                ApartmentMembership.user_id == user_id,
                Apartment.building_id == building_id,
                ACTIVE_MEMBERSHIP,
            )
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
                since=membership.started_at,
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


def _apartment_summary(apartment: Apartment, residents: list[CardResident]) -> ApartmentSummary:
    def count(relation: RelationType) -> int:
        return sum(1 for resident in residents if resident.relation_type == relation)

    return ApartmentSummary(
        apartment_id=apartment.id,
        apartment_number=apartment.number,
        entrance=apartment.entrance,
        floor=apartment.floor,
        area_m2=apartment.area_m2,
        owner_count=count(RelationType.OWNER),
        tenant_count=count(RelationType.TENANT),
        resident_count=count(RelationType.RESIDENT),
    )


def get_property_service(session: DbSession) -> PropertyService:
    return PropertyService(session)
