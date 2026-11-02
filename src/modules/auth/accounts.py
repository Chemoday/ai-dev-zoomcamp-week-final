"""Residents, roles, ending memberships and closing accounts (task E7/A4, docs/API.md section 7,
"Residents, roles and closing accounts").

Closing is the only way to take access away: it removes the personal data, signs the person out
everywhere, deletes their links and the invitations to their e-mail, and stores the durable event
UserAccountClosed (ids only) in the same transaction, so notifications drops what it kept.
An account closes by itself when its last membership ends and it has no staff role.
"""

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.event_bus import EventOutbox
from src.modules.auth.errors import forbidden, membership_not_found, user_not_found
from src.modules.auth.events import UserAccountClosed
from src.modules.auth.invitations import InvitationService
from src.modules.auth.mail import AuthMail
from src.modules.auth.models import AuthToken
from src.modules.auth.schemas import (
    InvitationView,
    PeopleResponse,
    PersonMembershipView,
    PersonView,
    ResidentsResponse,
    ResidentView,
    RoleUpdateResponse,
)
from src.modules.auth.service import AuthService
from src.modules.property.schemas import (
    ApartmentRefDTO,
    BuildingRole,
    CurrentUserDTO,
    RelationType,
    UserRefDTO,
)
from src.modules.property.service import PropertyService


class AccountService:
    def __init__(self, session: AsyncSession, settings: Settings, outbox: EventOutbox) -> None:
        self.session = session
        self.settings = settings
        self.outbox = outbox
        self.properties = PropertyService(session)
        self.invitations = InvitationService(session, settings)

    # --- An apartment's residents (owners of the apartment, the chairman) ----------------------

    async def residents(self, caller: CurrentUserDTO, apartment_id: uuid.UUID) -> ResidentsResponse:
        await self._owner_or_chairman(caller, apartment_id)
        residents = await self.properties.apartment_residents(apartment_id)
        emails = await self.invitations.invited_emails_by_membership(
            caller.id, [r.membership_id for r in residents]
        )
        return ResidentsResponse(
            residents=[
                ResidentView(
                    user_id=r.user_id,
                    membership_id=r.membership_id,
                    relation_type=r.relation_type,
                    display_label=r.display_label,
                    since=r.since,
                    invited_email=emails.get(r.membership_id),
                )
                for r in residents
            ],
            pending_invitations=await self.invitations.open_for_apartment(caller, apartment_id),
        )

    async def remove_tenant(
        self, caller: CurrentUserDTO, apartment_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """An owner ends a tenant's membership in their apartment."""
        await self._owner(caller, apartment_id)
        await self._end_tenant(apartment_id, user_id, caller.building_id)

    async def replace_tenant(
        self, caller: CurrentUserDTO, apartment_id: uuid.UUID, user_id: uuid.UUID, email: str
    ) -> tuple[InvitationView, AuthMail]:
        """One transaction: end the old tenant's membership, then invite the new e-mail (the
        tenant limit counts without the replaced tenant). Any error undoes both."""
        apartment = await self._owner(caller, apartment_id)
        await self._end_tenant(apartment_id, user_id, caller.building_id)
        return await self.invitations.invite_tenant(
            caller, apartment.apartment_id, apartment.number, email
        )

    # --- The chairman's administration -------------------------------------------------------

    async def people(self, caller: CurrentUserDTO) -> PeopleResponse:
        self._chairman(caller)
        people = await self.properties.building_people(caller.building_id)
        by_membership = await self.invitations.invited_emails_by_membership(
            caller.id, [m.membership_id for p in people for m in p.memberships]
        )
        by_staff = await self.invitations.invited_emails_by_staff(
            caller.id,
            {p.user_id: p.building_role for p in people if p.building_role != BuildingRole.USER},
        )
        return PeopleResponse(
            people=[
                PersonView(
                    user_id=p.user_id,
                    is_me=p.user_id == caller.id,
                    display_label=p.display_label,
                    building_role=p.building_role,
                    invited_email=by_staff.get(p.user_id),
                    memberships=[
                        PersonMembershipView(
                            membership_id=m.membership_id,
                            apartment_id=m.apartment_id,
                            apartment_number=m.apartment_number,
                            relation_type=m.relation_type,
                            display_label=m.display_label,
                            since=m.since,
                            invited_email=by_membership.get(m.membership_id),
                        )
                        for m in p.memberships
                    ],
                )
                for p in people
            ]
        )

    async def end_membership(
        self, caller: CurrentUserDTO, user_id: uuid.UUID, membership_id: uuid.UUID
    ) -> None:
        self._chairman(caller)
        person = await self._person(caller, user_id)
        membership = await self.properties.membership_ref(membership_id)
        if membership is None or membership.user_id != person.user_id:
            raise membership_not_found()
        if not await self.properties.end_membership(membership_id):
            await self._close(person.user_id, person.building_id)

    async def set_role(
        self, caller: CurrentUserDTO, user_id: uuid.UUID, role: BuildingRole
    ) -> RoleUpdateResponse:
        """Change a role; USER for a person without a membership closes the account."""
        self._chairman(caller)
        person = await self._person(caller, user_id)
        await self.properties.set_building_role(person.user_id, role)  # LAST_CHAIRMAN
        closed = False
        if role == BuildingRole.USER:
            current = await self.properties.get_current_user(person.user_id)
            if current is not None and not current.memberships:
                await self._close(person.user_id, person.building_id)
                closed = True
        return RoleUpdateResponse(user_id=person.user_id, building_role=role, account_closed=closed)

    async def close(self, caller: CurrentUserDTO, user_id: uuid.UUID) -> None:
        self._chairman(caller)
        person = await self._person(caller, user_id)
        await self._close(person.user_id, person.building_id)

    # --- Internals ------------------------------------------------------------------------------

    @staticmethod
    def _chairman(caller: CurrentUserDTO) -> None:
        if caller.building_role != BuildingRole.CHAIRMAN:
            raise forbidden()

    async def _person(self, caller: CurrentUserDTO, user_id: uuid.UUID) -> UserRefDTO:
        person = await self.properties.user_ref(user_id)
        if person is None or person.building_id != caller.building_id:
            raise user_not_found()
        return person

    async def _apartment(self, caller: CurrentUserDTO, apartment_id: uuid.UUID) -> ApartmentRefDTO:
        apartment = await self.properties.apartment_ref(apartment_id)
        if apartment is None or apartment.building_id != caller.building_id:
            raise forbidden()  # an unknown apartment answers like someone else's
        return apartment

    async def _owner(self, caller: CurrentUserDTO, apartment_id: uuid.UUID) -> ApartmentRefDTO:
        apartment = await self._apartment(caller, apartment_id)
        if not await self.properties.is_owner_of(caller.id, apartment_id):
            raise forbidden()
        return apartment

    async def _owner_or_chairman(
        self, caller: CurrentUserDTO, apartment_id: uuid.UUID
    ) -> ApartmentRefDTO:
        apartment = await self._apartment(caller, apartment_id)
        if caller.building_role != BuildingRole.CHAIRMAN and not await self.properties.is_owner_of(
            caller.id, apartment_id
        ):
            raise forbidden()
        return apartment

    async def _end_tenant(
        self, apartment_id: uuid.UUID, user_id: uuid.UUID, building_id: uuid.UUID
    ) -> None:
        membership = await self.properties.active_membership(user_id, apartment_id)
        if membership is None or membership.relation_type != RelationType.TENANT:
            raise membership_not_found()
        if not await self.properties.end_membership(membership.membership_id):
            await self._close(user_id, building_id)

    async def _close(self, user_id: uuid.UUID, building_id: uuid.UUID) -> None:
        # The people lock before the invitation rows (the one lock order, see lock_people).
        await self.properties.lock_people(building_id)
        # The invitations to the e-mail are found while the account still has it.
        await self.invitations.drop_for_closed_account(user_id, building_id)
        if not await self.properties.close_account(user_id):  # LAST_CHAIRMAN
            return
        await AuthService(self.session, self.settings).revoke_all(user_id, reason="account_closed")
        await self.session.execute(
            delete(AuthToken)
            .where(AuthToken.user_id == user_id)
            .execution_options(synchronize_session=False)
        )
        self.outbox.add(UserAccountClosed(user_id=user_id, building_id=building_id))
