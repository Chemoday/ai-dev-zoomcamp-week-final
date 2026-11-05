"""Invitations, registration and accepting (task E7/A4, docs/API.md section 7, "Invitations").

An invitation is the only way into the system and the only way to another apartment or a staff
role. No account exists until it is accepted: a new person registers through the link; a person
with an account signs in and accepts. Nothing here tells an inviter whether an e-mail has an
account: every invitation answers the same and sends the same e-mail.

E-mails are returned as AuthMail values and sent by detached work after the commit, so the plain
token lives only in memory. Nothing personal is logged.
"""

import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.exceptions import DomainError
from src.modules.auth.errors import (
    apartment_not_found,
    conflict,
    forbidden,
    invalid_link,
    invitation_not_found,
    rate_limited,
)
from src.modules.auth.mail import AuthMail, invitation_target
from src.modules.auth.models import AuthAttempt, AuthInvitation, AuthToken
from src.modules.auth.policies import (
    INVITATION_RETENTION,
    INVITATION_TTL,
    INVITATIONS_PER_INVITER_PER_HOUR,
    REGISTRATION_TTL,
    invitation_status,
    keyed_hash,
    mask_email,
    sign_in_link,
    staff_invitation_applies,
    user_subject,
)
from src.modules.auth.schemas import (
    AttemptKind,
    AuthMethod,
    InvitationCreate,
    InvitationKind,
    InvitationPreview,
    InvitationStatus,
    InvitationView,
    IssuedSession,
    OwnerInvitationCreate,
    RegistrationRequest,
    SignInResponse,
    SignInStatus,
    StaffInvitationCreate,
    StaffRole,
    TenantInvitationCreate,
    TokenPurpose,
)
from src.modules.auth.service import AuthService
from src.modules.property.schemas import (
    BuildingRole,
    CurrentUserDTO,
    DisplayMode,
    RelationType,
)
from src.modules.property.service import PropertyService

HOUR = timedelta(hours=1)
TOKEN_BYTES = 32
_RELATION = {InvitationKind.OWNER: RelationType.OWNER, InvitationKind.TENANT: RelationType.TENANT}


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Applies:
    """Answers "does this invitation still apply?" with one query per apartment or building."""

    properties: PropertyService

    async def __call__(self, invitation: AuthInvitation, cache: dict[object, bool]) -> bool:
        if invitation.invited_by_user_id is None:
            return True  # sent by the system (the first chairman): no chairman needed
        if invitation.kind == InvitationKind.TENANT:
            assert invitation.apartment_id is not None
            key: object = ("owner", invitation.apartment_id)
            if key not in cache:
                cache[key] = await self.properties.has_active_owner(invitation.apartment_id)
            return cache[key]
        if invitation.apartment_id is not None:
            apartment_key = ("apartment", invitation.apartment_id)
            if apartment_key not in cache:
                ref = await self.properties.apartment_ref(invitation.apartment_id)
                cache[apartment_key] = ref is not None
            if not cache[apartment_key]:
                return False
        key = ("chairman", invitation.building_id)
        if key not in cache:
            cache[key] = await self.properties.active_chairman_count(invitation.building_id) > 0
        return cache[key]


class InvitationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.properties = PropertyService(session)
        self._applies = _Applies(self.properties)

    def _hash(self, value: str) -> str:
        return keyed_hash(self.settings.AUTH_HASH_SECRET.get_secret_value(), value)

    # --- Creating, resending, deleting ----------------------------------------------------------

    async def create(
        self, caller: CurrentUserDTO, body: InvitationCreate
    ) -> tuple[InvitationView, AuthMail]:
        """Invite someone (201). The answer is the same whether the e-mail has an account."""
        now = _now()
        apartment_id: uuid.UUID | None = None
        apartment_number: str | None = None
        if isinstance(body, OwnerInvitationCreate | StaffInvitationCreate):
            if caller.building_role != BuildingRole.CHAIRMAN:
                raise forbidden()
        if isinstance(body, OwnerInvitationCreate):
            apartment = await self.properties.apartment_by_number(
                caller.building_id, body.apartment_number
            )
            if apartment is None:
                raise apartment_not_found()
            apartment_id, apartment_number = apartment.apartment_id, apartment.number
        elif isinstance(body, TenantInvitationCreate):
            apartment = await self.properties.apartment_ref(body.apartment_id)
            if apartment is None or apartment.building_id != caller.building_id:
                raise apartment_not_found()
            if not await self.properties.is_owner_of(caller.id, apartment.apartment_id):
                raise forbidden()
            apartment_id, apartment_number = apartment.apartment_id, apartment.number
        building_role = body.building_role if isinstance(body, StaffInvitationCreate) else None
        return await self._invite(
            caller,
            kind=body.kind,
            email=body.email,
            apartment_id=apartment_id,
            apartment_number=apartment_number,
            building_role=building_role,
            now=now,
        )

    async def invite_tenant(
        self, caller: CurrentUserDTO, apartment_id: uuid.UUID, apartment_number: str, email: str
    ) -> tuple[InvitationView, AuthMail]:
        """A TENANT invitation whose caller is already known to own the apartment (replace)."""
        return await self._invite(
            caller,
            kind=InvitationKind.TENANT,
            email=email,
            apartment_id=apartment_id,
            apartment_number=apartment_number,
            building_role=None,
            now=_now(),
        )

    async def _invite(
        self,
        caller: CurrentUserDTO,
        *,
        kind: InvitationKind,
        email: str,
        apartment_id: uuid.UUID | None,
        apartment_number: str | None,
        building_role: StaffRole | None,
        now: datetime,
    ) -> tuple[InvitationView, AuthMail]:
        await self.properties.lock_people(caller.building_id)  # the one lock order, see there
        await self._check_limit(caller.id, now)
        if apartment_id is not None:
            await self.properties.lock_apartment(apartment_id)
        # One request per target at a time (a double click, two chairmen), so the second one
        # finds the first instead of failing on the unique index.
        target = f"{kind}|{email}|{apartment_id}|{building_role}"
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"auth:invite:{self._hash(target)}"},
        )
        # The unique index is installation-wide (one building per installation today, BACKLOG
        # B-1); the lookup stays in the caller's building.
        existing = await self.session.scalar(
            select(AuthInvitation)
            .where(
                AuthInvitation.building_id == caller.building_id,
                AuthInvitation.kind == kind,
                AuthInvitation.email == email,
                AuthInvitation.apartment_id.is_(None)
                if apartment_id is None
                else AuthInvitation.apartment_id == apartment_id,
                AuthInvitation.building_role.is_(None)
                if building_role is None
                else AuthInvitation.building_role == building_role,
                AuthInvitation.accepted_at.is_(None),
            )
            .with_for_update()
        )
        if existing is not None:
            if await self._status(existing, now, {}) == InvitationStatus.PENDING:
                raise conflict(
                    "INVITATION_ALREADY_PENDING",
                    "This person is already invited; resend the invitation instead.",
                )
            await self.session.delete(existing)  # expired or no longer valid: replaced
            await self.session.flush()
        if kind == InvitationKind.TENANT:
            assert apartment_id is not None
            await self._check_tenant_limit(apartment_id, now)
        invitation = AuthInvitation(
            building_id=caller.building_id,
            kind=kind,
            email=email,
            apartment_id=apartment_id,
            building_role=building_role,
            invited_by_user_id=caller.id,
            created_at=now,
            last_sent_at=now,
            expires_at=now + INVITATION_TTL,
        )
        self.session.add(invitation)
        await self.session.flush()
        token = await self._new_link(invitation, now)
        self._record_send(caller.id, now)
        await self.session.flush()
        view = (await self._views([invitation], caller.id, now))[0]
        return view, await self._mail(invitation, apartment_number, token)

    async def resend(
        self, caller: CurrentUserDTO, invitation_id: uuid.UUID
    ) -> tuple[InvitationView, AuthMail]:
        """A new link for a pending or expired invitation; the 7 days start again."""
        now = _now()
        await self.properties.lock_people(caller.building_id)
        invitation = await self._visible_for_update(caller, invitation_id)
        if invitation.accepted_at is not None:
            raise self._accepted()
        if not await self._applies(invitation, {}):
            raise conflict(
                "INVITATION_NO_LONGER_VALID",
                "This invitation no longer applies; delete it instead.",
            )
        await self._check_limit(caller.id, now)
        if invitation.kind == InvitationKind.TENANT and invitation.expires_at <= now:
            assert invitation.apartment_id is not None
            # An expired invitation does not count toward the limit; a resent one does again.
            await self.properties.lock_apartment(invitation.apartment_id)
            await self._check_tenant_limit(invitation.apartment_id, now)
        invitation.last_sent_at = now
        invitation.expires_at = now + INVITATION_TTL
        token = await self._new_link(invitation, now)
        self._record_send(caller.id, now)
        await self.session.flush()
        view = (await self._views([invitation], caller.id, now))[0]
        return view, await self._mail(invitation, view.apartment_number, token)

    async def delete(self, caller: CurrentUserDTO, invitation_id: uuid.UUID) -> None:
        await self.properties.lock_people(caller.building_id)
        invitation = await self._visible_for_update(caller, invitation_id)
        if invitation.accepted_at is not None:
            raise self._accepted()
        await self.session.delete(invitation)  # its links cascade
        await self.session.flush()

    # --- Listing ---------------------------------------------------------------------------------

    async def list_for(self, caller: CurrentUserDTO) -> list[InvitationView]:
        """The chairman sees the building's invitations; an owner the TENANT invitations of the
        apartments they own; anyone else none. Newest first."""
        query = select(AuthInvitation).where(AuthInvitation.building_id == caller.building_id)
        if caller.building_role != BuildingRole.CHAIRMAN:
            owned = self._owned_apartments(caller)
            if not owned:
                return []
            query = query.where(
                AuthInvitation.kind == InvitationKind.TENANT,
                AuthInvitation.apartment_id.in_(owned),
            )
        rows = list(
            await self.session.scalars(
                query.order_by(AuthInvitation.created_at.desc(), AuthInvitation.id)
            )
        )
        return await self._views(rows, caller.id, _now())

    async def open_for_apartment(
        self, caller: CurrentUserDTO, apartment_id: uuid.UUID
    ) -> list[InvitationView]:
        """Not yet accepted invitations of one apartment that the caller may see (residents)."""
        query = select(AuthInvitation).where(
            AuthInvitation.apartment_id == apartment_id,
            AuthInvitation.building_id == caller.building_id,
            AuthInvitation.accepted_at.is_(None),
        )
        if caller.building_role != BuildingRole.CHAIRMAN:
            query = query.where(AuthInvitation.kind == InvitationKind.TENANT)
        rows = list(
            await self.session.scalars(
                query.order_by(AuthInvitation.created_at.desc(), AuthInvitation.id)
            )
        )
        return await self._views(rows, caller.id, _now())

    # --- The invitation link -------------------------------------------------------------------

    async def open_link(self, token_row: AuthToken, now: datetime) -> SignInResponse | None:
        """Redeeming an invitation link: a registration form for a new person, or "sign in to
        accept" for an existing account. None when the link cannot be used."""
        invitation = await self._usable_invitation(token_row, now)
        if invitation is None:
            return None
        preview = await self._preview(invitation)
        account = await self.properties.find_active_user_by_email(invitation.email)
        if account is not None:
            if account.building_id != invitation.building_id:
                return None
            # Nothing is applied and the link is not used up: the person signs in and accepts.
            return SignInResponse(status=SignInStatus.SIGN_IN_TO_ACCEPT, invitation=preview)
        await self.session.execute(
            delete(AuthToken)
            .where(
                AuthToken.invitation_id == invitation.id,
                AuthToken.purpose == TokenPurpose.REGISTRATION,
            )
            .execution_options(synchronize_session=False)
        )
        registration = secrets.token_urlsafe(TOKEN_BYTES)
        self.session.add(
            AuthToken(
                purpose=TokenPurpose.REGISTRATION,
                token_hash=self._hash(registration),
                invitation_id=invitation.id,
                created_at=now,
                expires_at=now + REGISTRATION_TTL,
            )
        )
        await self.session.flush()
        return SignInResponse(
            status=SignInStatus.REGISTRATION_REQUIRED,
            registration_token=registration,
            expires_in=int(REGISTRATION_TTL.total_seconds()),
            invitation=preview,
        )

    async def register(self, body: RegistrationRequest, user_agent: str | None) -> IssuedSession:
        """Create the invited person's account with its membership or role, and sign them in."""
        now = _now()
        token_hash = self._hash(body.registration_token)
        # The people lock before any row lock (the one order of PropertyService.lock_people):
        # find the building without a lock, then lock in order.
        building_id = await self.session.scalar(
            select(AuthInvitation.building_id)
            .join(AuthToken, AuthToken.invitation_id == AuthInvitation.id)
            .where(
                AuthToken.token_hash == token_hash, AuthToken.purpose == TokenPurpose.REGISTRATION
            )
        )
        if building_id is None:
            raise invalid_link()
        await self.properties.lock_people(building_id)
        await self.lock_invitation_of(token_hash)  # the invitation before its token rows
        token_row = await self.session.scalar(
            select(AuthToken)
            .where(
                AuthToken.token_hash == token_hash, AuthToken.purpose == TokenPurpose.REGISTRATION
            )
            .with_for_update()
        )
        if token_row is None or token_row.used_at is not None or token_row.expires_at <= now:
            raise invalid_link()
        invitation = await self._invitation_for_update(token_row.invitation_id)
        if (
            invitation is None
            or invitation.accepted_at is not None
            or invitation.expires_at <= now  # an expired one no longer holds a tenant's place
            or not await self._applies(invitation, {})
        ):
            raise invalid_link()
        # One registration per e-mail at a time, so two tabs cannot both create the account.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"auth:register:{self._hash('email:' + invitation.email)}"},
        )
        if await self.properties.find_active_user_by_email(invitation.email) is not None:
            raise conflict(
                "EMAIL_ALREADY_REGISTERED",
                "An account with this e-mail exists; sign in and accept the invitation.",
            )
        role = (
            BuildingRole(invitation.building_role)
            if invitation.kind == InvitationKind.STAFF and invitation.building_role
            else BuildingRole.USER
        )
        user_id = await self.properties.create_user(
            invitation.building_id,
            email=invitation.email,
            full_name=body.full_name,
            phone=body.phone,
            locale=body.locale,
            building_role=role,
        )
        membership_id = None
        if invitation.kind in _RELATION:
            assert invitation.apartment_id is not None
            membership_id = await self.properties.open_membership(
                user_id,
                invitation.apartment_id,
                _RELATION[invitation.kind],
                body.privacy_display_mode,
            )
        self._mark_accepted(invitation, user_id, membership_id, applied=True, now=now)
        await self._drop_links(invitation.id)
        return await AuthService(self.session, self.settings).start_session(
            user_id, AuthMethod.REGISTRATION, user_agent
        )

    async def accept(self, caller: CurrentUserDTO, token: str) -> bool:
        """An existing account accepts after signing in; True when anything changed. It never
        lowers anything: an existing membership stays, a staff role is not replaced."""
        now = _now()
        # Before any invitation row: closing an account takes this lock, then deletes rows.
        await self.properties.lock_people(caller.building_id)
        await self.lock_invitation_of(self._hash(token))
        token_row = await self.session.scalar(
            select(AuthToken)
            .where(
                AuthToken.token_hash == self._hash(token),
                AuthToken.purpose == TokenPurpose.INVITATION,
            )
            .with_for_update()
        )
        if token_row is None:
            raise invalid_link()
        invitation = await self._usable_invitation(token_row, now)
        if invitation is None or invitation.building_id != caller.building_id:
            raise invalid_link()
        account = await self.properties.find_active_user_by_email(invitation.email)
        if account is None or account.user_id != caller.id:
            raise invalid_link()  # another person's invitation: no hint why
        membership_id = None
        if invitation.kind in _RELATION:
            assert invitation.apartment_id is not None
            membership_id = await self.properties.open_membership(
                caller.id,
                invitation.apartment_id,
                _RELATION[invitation.kind],
                DisplayMode.APARTMENT_ONLY,
            )
            applied = membership_id is not None
            if not applied and invitation.kind == InvitationKind.OWNER:
                # Raising is not lowering: a tenant (or family member) who bought the flat.
                current = await self.properties.active_membership(
                    caller.id, invitation.apartment_id
                )
                if current is not None and await self.properties.raise_to_owner(
                    current.membership_id
                ):
                    applied, membership_id = True, current.membership_id
        else:
            assert invitation.building_role is not None
            applied = staff_invitation_applies(account.building_role, invitation.building_role)
            if applied:
                await self.properties.set_building_role(
                    caller.id, BuildingRole(invitation.building_role)
                )
        self._mark_accepted(invitation, caller.id, membership_id, applied=applied, now=now)
        await self._drop_links(invitation.id)
        await self.session.flush()
        return applied

    async def resend_for_sign_in(self, email: str, now: datetime) -> list[AuthMail]:
        """`POST /auth/magic-link` for an e-mail without an account: each of its pending
        invitations gets a new link with the same expiry (the old links stop working)."""
        rows = list(
            await self.session.scalars(
                select(AuthInvitation)
                .where(
                    AuthInvitation.email == email,
                    AuthInvitation.accepted_at.is_(None),
                    AuthInvitation.expires_at > now,
                )
                .order_by(AuthInvitation.created_at)
                .with_for_update()
            )
        )
        mails = []
        cache: dict[object, bool] = {}
        for invitation in rows:
            if not await self._applies(invitation, cache):
                continue
            token = await self._new_link(invitation, now)
            number = await self._apartment_number(invitation)
            mails.append(await self._mail(invitation, number, token))
        return mails

    # --- For the system (the first chairman) -----------------------------------------------------

    async def invite_first_chairman(
        self, building_id: uuid.UUID, email: str
    ) -> tuple[uuid.UUID, AuthMail]:
        """A STAFF invitation with CHAIRMAN on behalf of the system; replaces an open one."""
        now = _now()
        await self.session.execute(
            delete(AuthInvitation)
            .where(
                AuthInvitation.kind == InvitationKind.STAFF,
                AuthInvitation.email == email,
                AuthInvitation.building_role == StaffRole.CHAIRMAN,
                AuthInvitation.accepted_at.is_(None),
            )
            .execution_options(synchronize_session=False)
        )
        invitation = AuthInvitation(
            building_id=building_id,
            kind=InvitationKind.STAFF,
            email=email,
            building_role=StaffRole.CHAIRMAN,
            invited_by_user_id=None,
            created_at=now,
            last_sent_at=now,
            expires_at=now + INVITATION_TTL,
        )
        self.session.add(invitation)
        await self.session.flush()
        token = await self._new_link(invitation, now)
        return invitation.id, await self._mail(invitation, None, token)

    # --- E-mails a caller typed (residents, people) ---------------------------------------------

    async def invited_emails_by_membership(
        self, caller_id: uuid.UUID, membership_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """For each membership an invitation of the caller created or reopened: its e-mail."""
        ids = list(membership_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(AuthInvitation.membership_id, AuthInvitation.email)
            .where(
                AuthInvitation.membership_id.in_(ids),
                AuthInvitation.invited_by_user_id == caller_id,
            )
            .order_by(AuthInvitation.accepted_at)  # the newest wins below
        )
        return {membership_id: email for membership_id, email in rows.all() if membership_id}

    async def invited_emails_by_staff(
        self, caller_id: uuid.UUID, roles: dict[uuid.UUID, BuildingRole]
    ) -> dict[uuid.UUID, str]:
        """For each person: the e-mail of the newest applied STAFF invitation the caller typed for
        the person's current role."""
        if not roles:
            return {}
        rows = await self.session.execute(
            select(
                AuthInvitation.accepted_user_id,
                AuthInvitation.building_role,
                AuthInvitation.email,
            )
            .where(
                AuthInvitation.kind == InvitationKind.STAFF,
                AuthInvitation.applied.is_(True),
                AuthInvitation.accepted_user_id.in_(list(roles)),
                AuthInvitation.invited_by_user_id == caller_id,
            )
            .order_by(AuthInvitation.accepted_at)
        )
        found: dict[uuid.UUID, str] = {}
        for user_id, role, email in rows.all():
            if user_id is not None and roles.get(user_id) == role:
                found[user_id] = email
        return found

    async def drop_for_closed_account(self, user_id: uuid.UUID, building_id: uuid.UUID) -> None:
        """Before an account is closed: delete the invitations to its e-mail (open ones, and the
        accepted ones, which still hold the address). The caller holds the people lock."""
        emails = set(
            await self.session.scalars(
                select(AuthInvitation.email).where(
                    AuthInvitation.building_id == building_id,
                    AuthInvitation.accepted_at.is_(None),
                )
            )
        )
        owners = await self.properties.active_users_by_emails(emails)
        own = [email for email, owner in owners.items() if owner == user_id]
        conditions = [AuthInvitation.accepted_user_id == user_id]
        if own:
            conditions.append(
                and_(AuthInvitation.email.in_(own), AuthInvitation.accepted_at.is_(None))
            )
        for condition in conditions:
            await self.session.execute(
                delete(AuthInvitation).where(condition).execution_options(synchronize_session=False)
            )

    # --- Internals ------------------------------------------------------------------------------

    @staticmethod
    def _accepted() -> DomainError:
        return conflict(
            "INVITATION_ACCEPTED",
            "The invitation was accepted; to take access away, end the membership.",
        )

    @staticmethod
    def _owned_apartments(caller: CurrentUserDTO) -> list[uuid.UUID]:
        return [m.apartment_id for m in caller.memberships if m.relation_type == RelationType.OWNER]

    async def _visible_for_update(
        self, caller: CurrentUserDTO, invitation_id: uuid.UUID
    ) -> AuthInvitation:
        """The chairman reaches every invitation of the building; an owner the TENANT ones of
        their apartments; otherwise it does not exist (404)."""
        invitation = await self._invitation_for_update(invitation_id)
        if invitation is None or invitation.building_id != caller.building_id:
            raise invitation_not_found()
        if caller.building_role == BuildingRole.CHAIRMAN:
            return invitation
        if (
            invitation.kind == InvitationKind.TENANT
            and invitation.apartment_id in self._owned_apartments(caller)
        ):
            return invitation
        raise invitation_not_found()

    async def lock_invitation_of(self, token_hash: str) -> None:
        """Lock the invitation of a link before the link's own row: the order is the people lock,
        an apartment, the invitation, then its tokens (registering in one tab while the link opens
        in another must not deadlock). A token without an invitation locks nothing."""
        invitation_id = await self.session.scalar(
            select(AuthToken.invitation_id).where(AuthToken.token_hash == token_hash)
        )
        await self._invitation_for_update(invitation_id)

    async def _invitation_for_update(
        self, invitation_id: uuid.UUID | None
    ) -> AuthInvitation | None:
        # A path that later changes people must hold PropertyService.lock_people already, or
        # it can deadlock with one that took the people lock first.
        if invitation_id is None:
            return None
        return await self.session.scalar(
            select(AuthInvitation).where(AuthInvitation.id == invitation_id).with_for_update()
        )

    async def _usable_invitation(
        self, token_row: AuthToken, now: datetime
    ) -> AuthInvitation | None:
        if token_row.used_at is not None or token_row.expires_at <= now:
            return None
        invitation = await self._invitation_for_update(token_row.invitation_id)
        if (
            invitation is None
            or invitation.accepted_at is not None
            or invitation.expires_at <= now
            or not await self._applies(invitation, {})
        ):
            return None
        return invitation

    async def _check_limit(self, inviter_id: uuid.UUID, now: datetime) -> None:
        """At most 10 invitations and resends per inviter per hour (429, not silent)."""
        subject = self._hash(user_subject(inviter_id))
        recent = list(
            await self.session.scalars(
                select(AuthAttempt.attempted_at)
                .where(
                    AuthAttempt.kind == AttemptKind.INVITATION,
                    AuthAttempt.subject_hash == subject,
                    AuthAttempt.attempted_at > now - HOUR,
                )
                .order_by(AuthAttempt.attempted_at)
            )
        )
        if len(recent) >= INVITATIONS_PER_INVITER_PER_HOUR:
            raise rate_limited(max(int((recent[0] + HOUR - now).total_seconds()), 1))

    def _record_send(self, inviter_id: uuid.UUID, now: datetime) -> None:
        self.session.add(
            AuthAttempt(
                kind=AttemptKind.INVITATION,
                subject_hash=self._hash(user_subject(inviter_id)),
                succeeded=True,
                attempted_at=now,
            )
        )

    async def _check_tenant_limit(self, apartment_id: uuid.UUID, now: datetime) -> None:
        """Active tenants plus pending tenant invitations, under the apartment's lock."""
        pending = await self.session.scalar(
            select(func.count(AuthInvitation.id)).where(
                AuthInvitation.kind == InvitationKind.TENANT,
                AuthInvitation.apartment_id == apartment_id,
                AuthInvitation.accepted_at.is_(None),
                AuthInvitation.expires_at > now,
            )
        )
        tenants = await self.properties.active_tenant_count(apartment_id)
        if tenants + int(pending or 0) >= self.settings.MAX_TENANTS_PER_APARTMENT:
            raise conflict(
                "TENANT_LIMIT_REACHED",
                "The apartment has the most tenants allowed, pending invitations included.",
            )

    async def _new_link(self, invitation: AuthInvitation, now: datetime) -> str:
        """A new invitation link that lives as long as the invitation; every older link of the
        invitation (and a registration form in progress) stops working."""
        await self._drop_links(invitation.id)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.session.add(
            AuthToken(
                purpose=TokenPurpose.INVITATION,
                token_hash=self._hash(token),
                invitation_id=invitation.id,
                created_at=now,
                expires_at=invitation.expires_at,
            )
        )
        await self.session.flush()
        return token

    async def _drop_links(self, invitation_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(AuthToken)
            .where(AuthToken.invitation_id == invitation_id)
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    def _mark_accepted(
        invitation: AuthInvitation,
        user_id: uuid.UUID,
        membership_id: uuid.UUID | None,
        *,
        applied: bool,
        now: datetime,
    ) -> None:
        invitation.accepted_at = now
        invitation.accepted_user_id = user_id
        invitation.membership_id = membership_id
        invitation.applied = applied

    async def _status(
        self, invitation: AuthInvitation, now: datetime, cache: dict[object, bool]
    ) -> InvitationStatus:
        return invitation_status(
            accepted=invitation.accepted_at is not None,
            applies=invitation.accepted_at is not None or await self._applies(invitation, cache),
            expires_at=invitation.expires_at,
            now=now,
        )

    async def _apartment_number(self, invitation: AuthInvitation) -> str | None:
        if invitation.apartment_id is None:
            return None
        ref = await self.properties.apartment_ref(invitation.apartment_id)
        return ref.number if ref else None

    async def _preview(self, invitation: AuthInvitation) -> InvitationPreview:
        return InvitationPreview(
            kind=invitation.kind,
            apartment_number=await self._apartment_number(invitation),
            building_role=invitation.building_role,
        )

    async def _views(
        self, rows: list[AuthInvitation], caller_id: uuid.UUID, now: datetime
    ) -> list[InvitationView]:
        cache: dict[object, bool] = {}
        numbers: dict[uuid.UUID, str | None] = {}
        views = []
        for invitation in rows:
            if invitation.apartment_id is not None and invitation.apartment_id not in numbers:
                numbers[invitation.apartment_id] = await self._apartment_number(invitation)
            mine = invitation.invited_by_user_id == caller_id
            views.append(
                InvitationView(
                    invitation_id=invitation.id,
                    kind=invitation.kind,
                    email=invitation.email if mine else mask_email(invitation.email),
                    apartment_id=invitation.apartment_id,
                    apartment_number=numbers.get(invitation.apartment_id)
                    if invitation.apartment_id
                    else None,
                    building_role=invitation.building_role,
                    status=await self._status(invitation, now, cache),
                    mine=mine,
                    created_at=invitation.created_at,
                    expires_at=invitation.expires_at,
                )
            )
        return views

    async def _mail(
        self, invitation: AuthInvitation, apartment_number: str | None, token: str
    ) -> AuthMail:
        """The invitation e-mail, in the language of the account if the e-mail has one (only the
        mailbox owner sees which), else the instance default."""
        account = await self.properties.find_active_user_by_email(invitation.email)
        locale = (account.locale if account else None) or self.settings.DEFAULT_LOCALE
        return AuthMail(
            email=invitation.email,
            locale=locale,
            kind="invitation",
            link=sign_in_link(self.settings.PUBLIC_BASE_URL, token),
            values={
                "target": invitation_target(
                    locale, invitation.kind, apartment_number, invitation.building_role
                )
            },
        )


async def delete_old_invitations(session: AsyncSession, now: datetime) -> int:
    """For the clean-up loop (docs/SCHEMA.md 7, retention). Kept while they explain something:
    - an open invitation: until 30 days after it expired;
    - an accepted one that created or reopened a membership: until 30 days after that ended;
    - an applied STAFF invitation: while its person is active and holds that role;
    - any other accepted one: 30 days after acceptance.
    """
    cutoff = now - INVITATION_RETENTION
    properties = PropertyService(session)
    doomed: list[uuid.UUID] = list(
        await session.scalars(
            select(AuthInvitation.id).where(
                AuthInvitation.accepted_at.is_(None), AuthInvitation.expires_at < cutoff
            )
        )
    )
    accepted = (
        await session.execute(
            select(
                AuthInvitation.id,
                AuthInvitation.kind,
                AuthInvitation.applied,
                AuthInvitation.membership_id,
                AuthInvitation.accepted_user_id,
                AuthInvitation.building_role,
                AuthInvitation.accepted_at,
            ).where(AuthInvitation.accepted_at.is_not(None))
        )
    ).all()
    ended = await properties.membership_end_times(
        row.membership_id for row in accepted if row.membership_id is not None
    )
    staff = [
        row
        for row in accepted
        if row.membership_id is None and row.kind == InvitationKind.STAFF and row.applied
    ]
    staff_ids = {row.id for row in staff}
    roles = await properties.building_roles(
        row.accepted_user_id for row in staff if row.accepted_user_id is not None
    )
    for row in accepted:
        if row.membership_id is not None:
            end = ended.get(row.membership_id)
            if end is not None and end < cutoff:
                doomed.append(row.id)
        elif row.id in staff_ids:
            if row.accepted_user_id is None or roles.get(row.accepted_user_id) != row.building_role:
                doomed.append(row.id)
        elif row.accepted_at is not None and row.accepted_at < cutoff:
            doomed.append(row.id)
    if not doomed:
        return 0
    result = await session.execute(
        delete(AuthInvitation)
        .where(AuthInvitation.id.in_(doomed))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0
