"""The optional password (task E7/A5, docs/API.md section 7, docs/epics/E7_LOGIN.md 3.5).

- **Storage:** Argon2id in core_user_identities (provider "password"), rehashed on sign-in when the
  parameters change. Hashes run in worker threads, at most MAX_CONCURRENT_HASHES at a time, so a
  burst of logins cannot use up the memory of a small server.
- **One answer:** every failed login is the same 401 after exactly one hash: an unknown e-mail, a
  closed account, no password and a refused password are checked against a dummy hash.
- **Throttling without lockout:** failures are counted in auth_attempts by keyed hashes of the
  e-mail, the IP address and both together. A check reserves its failure and commits BEFORE the
  hash runs, then turns it into a success when the password was right; so parallel guesses all
  see each other and cannot slip past the limits. After 100 failures for an e-mail in a day its
  password waits for a sign-in with an e-mail link.

The password, the e-mail and the address never reach a log line, a span or a metric.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from opentelemetry import trace
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.exceptions import DomainError
from src.core.security import TokenClaims
from src.modules.auth.errors import (
    invalid_credentials,
    password_too_weak,
    rate_limited,
    reauthentication_required,
    unknown_user,
)
from src.modules.auth.mail import AuthMail
from src.modules.auth.models import AuthAttempt, UserIdentity
from src.modules.auth.policies import (
    PASSWORD_FAILURES_PER_EMAIL_AND_IP,
    PASSWORD_FAILURES_PER_EMAIL_PER_DAY,
    PASSWORD_FAILURES_PER_IP,
    PASSWORD_PROVIDER,
    PASSWORD_WINDOW,
    email_subject,
    ip_subject,
    is_recent_sign_in,
    keyed_hash,
    normalize_password,
    pair_subject,
    password_wait,
    password_weakness,
    user_subject,
)
from src.modules.auth.schemas import AttemptKind, AuthMethod, IssuedSession
from src.modules.auth.service import AuthService, sign_ins
from src.modules.property.schemas import CurrentUserDTO
from src.modules.property.service import PropertyService

logger = structlog.get_logger(__name__)
MAX_CONCURRENT_HASHES = 2
# More password checks than this waiting (running included) answer 429 at once: a flood of logins
# must not make everyone wait for seconds. The answer does not depend on the e-mail.
MAX_QUEUED_HASHES = 32
BUSY_RETRY_SECONDS = 5
DAY = timedelta(days=1)
PASSWORD_MAIL = "password_changed"


def _now() -> datetime:
    return datetime.now(UTC)


class PasswordHashing:
    """Argon2id (argon2-cffi defaults: RFC 9106, 3 passes, 64 MiB, 4 lanes) off the event loop.

    One instance per app (app.state.auth_passwords); tests swap in cheap parameters.
    """

    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self.hasher = hasher or PasswordHasher()
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_HASHES)
        self._queued = 0
        self._dummy: str | None = None

    @contextlib.contextmanager
    def admitted(self) -> Iterator[None]:
        """Count one password check or new hash from its start until its hash ended; 429 at once
        when too many are counted already."""
        if self._queued >= MAX_QUEUED_HASHES:
            raise rate_limited(BUSY_RETRY_SECONDS)
        self._queued += 1
        try:
            yield
        finally:
            self._queued -= 1

    async def _run(self, work: Callable[..., Any], *args: Any) -> Any:
        async with self._slots:
            return await asyncio.to_thread(work, *args)

    async def warm_up(self) -> None:
        """Make the dummy hash at startup, so even the first unknown e-mail costs one hash."""
        if self._dummy is None:
            self._dummy = await self._run(self.hasher.hash, uuid.uuid4().hex)

    async def hash(self, password: str) -> str:
        result: str = await self._run(self.hasher.hash, password)
        return result

    async def verify(self, encoded: str | None, password: str) -> bool:
        """Whether `password` matches; None is checked against the dummy hash (always False)."""
        if encoded is None:
            await self.warm_up()
        try:
            await self._run(self.hasher.verify, encoded or self._dummy, password)
        except (VerificationError, InvalidHashError):
            return False
        return encoded is not None

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self.hasher.check_needs_rehash(encoded)
        except InvalidHashError:
            return True


async def clear_password_refusal(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, now: datetime
) -> None:
    """A sign-in with an e-mail link lifts the refusal after too many failed logins, and starts
    the daily count of the e-mail again (it counts failures since the last success)."""
    contact = await PropertyService(session).get_delivery_contact(user_id)
    if contact is not None:
        session.add(
            AuthAttempt(
                kind=AttemptKind.PASSWORD,
                subject_hash=keyed_hash(
                    settings.AUTH_HASH_SECRET.get_secret_value(), email_subject(contact.email)
                ),
                succeeded=True,
                attempted_at=now,
            )
        )
    await session.execute(
        update(UserIdentity)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == PASSWORD_PROVIDER,
            UserIdentity.requires_link_sign_in.is_(True),
        )
        .values(requires_link_sign_in=False)
        .execution_options(synchronize_session=False)
    )


async def delete_identities(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Closing an account deletes all its ways to sign in."""
    await session.execute(
        delete(UserIdentity)
        .where(UserIdentity.user_id == user_id)
        .execution_options(synchronize_session=False)
    )


class PasswordService:
    def __init__(self, session: AsyncSession, settings: Settings, hashing: PasswordHashing) -> None:
        self.session = session
        self.settings = settings
        self.hashing = hashing
        self.properties = PropertyService(session)

    def _hash(self, value: str) -> str:
        return keyed_hash(self.settings.AUTH_HASH_SECRET.get_secret_value(), value)

    # --- Reading --------------------------------------------------------------------------------

    async def _identity(self, user_id: uuid.UUID) -> UserIdentity | None:
        identity: UserIdentity | None = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.user_id == user_id, UserIdentity.provider == PASSWORD_PROVIDER
            )
        )
        return identity

    async def is_set(self, user_id: uuid.UUID) -> bool:
        """Stays True while the password is refused after too many failures."""
        return await self._identity(user_id) is not None

    # --- Signing in -----------------------------------------------------------------------------

    async def login(
        self, email: str, password: str, address: str, user_agent: str | None
    ) -> IssuedSession:
        span = trace.get_current_span()
        span.set_attribute("auth.method", AuthMethod.PASSWORD.value)
        try:
            user_id = await self._check(email, address, normalize_password(password), user_id=None)
        except DomainError as exc:
            if exc.error_code == "RATE_LIMITED":
                span.set_attribute("auth.outcome", "throttled")
                sign_ins.add(1, {"method": AuthMethod.PASSWORD.value, "outcome": "throttled"})
            raise
        if user_id is None:
            span.set_attribute("auth.outcome", "failed")
            sign_ins.add(1, {"method": AuthMethod.PASSWORD.value, "outcome": "failed"})
            raise invalid_credentials()
        issued = await AuthService(self.session, self.settings).start_session(
            user_id, AuthMethod.PASSWORD, user_agent
        )
        span.set_attribute("auth.outcome", "signed_in")
        sign_ins.add(1, {"method": AuthMethod.PASSWORD.value, "outcome": "signed_in"})
        return issued

    async def _check(
        self, email: str, address: str, password: str, *, user_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        """One counted password check; the user id when the password is right, else None.

        `user_id` None means a login: the account is looked up by the e-mail. The reserved
        failure is committed before the hash; the queries do not depend on whether the e-mail has
        an account or a password, so neither does the timing.
        """
        with self.hashing.admitted():
            return await self._admitted_check(email, address, password, user_id)

    async def _admitted_check(
        self, email: str, address: str, password: str, user_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        now = _now()
        subjects = (
            pair_subject(email, address),
            ip_subject(address),
            email_subject(email),
        )
        # One check per address and per e-mail at a time while counting (address first, as in
        # links.py); the locks end with the commit below, before the slow hash.
        for subject in (ip_subject(address), email_subject(email)):
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"auth:password:{self._hash(subject)}"},
            )
        if wait := await self._wait(subjects, now):
            raise rate_limited(wait)
        reserved = [
            AuthAttempt(
                kind=AttemptKind.PASSWORD,
                subject_hash=self._hash(subject),
                succeeded=False,
                attempted_at=now,
            )
            for subject in subjects
        ]
        self.session.add_all(reserved)
        await self.session.flush()
        if user_id is None:
            found = await self.properties.find_active_user_by_email(email)
            user_id = found.user_id if found is not None else None
        # Always one lookup, so an unknown e-mail costs the same queries as a known one.
        identity = await self._identity(user_id or uuid.uuid4())
        refused = identity is None or identity.requires_link_sign_in
        encoded = None if refused or identity is None else identity.secret_data
        identity_id = identity.id if identity is not None else None
        # The day's failures of the e-mail, this one included (it is flushed): counted for every
        # e-mail, known or not.
        failures_today = len(await self._failures(email_subject(email), now - DAY))
        await self.session.commit()

        if not await self.hashing.verify(encoded, password):
            if failures_today >= PASSWORD_FAILURES_PER_EMAIL_PER_DAY:
                await self._refuse(identity_id)
            await self.session.commit()  # the failure counts even though the caller raises
            return None
        # Before any statement: no transaction stays open while a hash waits for its slot.
        rehashed = (
            await self.hashing.hash(password)
            if encoded is not None and self.hashing.needs_rehash(encoded)
            else None
        )
        await self.session.execute(
            update(AuthAttempt)
            .where(AuthAttempt.id.in_([attempt.id for attempt in reserved]))
            .values(succeeded=True)
            .execution_options(synchronize_session=False)
        )
        if rehashed is not None:
            await self.session.execute(
                update(UserIdentity)
                .where(UserIdentity.id == identity_id)
                .values(secret_data=rehashed)
                .execution_options(synchronize_session=False)
            )
        if not await self.properties.is_user_active(user_id):  # closed meanwhile
            await self.session.rollback()
            return None
        return user_id

    async def _failures(
        self, subject: str, since: datetime, *, reset_on_success: bool = True
    ) -> list[datetime]:
        """Failures of the subject after `since`, oldest first. With `reset_on_success`, only
        those after its last success: a success (or, for the e-mail, a sign-in with an e-mail
        link) starts the count again. Never for an address: one person's own sign-in must not
        reset the limit for guesses at other e-mails from the same address."""
        if not reset_on_success:
            return await self._failures_after(subject, since)
        last_success = await self.session.scalar(
            select(func.max(AuthAttempt.attempted_at)).where(
                AuthAttempt.kind == AttemptKind.PASSWORD,
                AuthAttempt.subject_hash == self._hash(subject),
                AuthAttempt.succeeded.is_(True),
                AuthAttempt.attempted_at > since,
            )
        )
        return await self._failures_after(subject, last_success or since)

    async def _failures_after(self, subject: str, since: datetime) -> list[datetime]:
        rows = await self.session.scalars(
            select(AuthAttempt.attempted_at)
            .where(
                AuthAttempt.kind == AttemptKind.PASSWORD,
                AuthAttempt.subject_hash == self._hash(subject),
                AuthAttempt.succeeded.is_(False),
                AuthAttempt.attempted_at > since,
            )
            .order_by(AuthAttempt.attempted_at)
        )
        return list(rows)

    async def _wait(self, subjects: tuple[str, str, str], now: datetime) -> int | None:
        """The growing delay of the e-mail and address together, or of the address alone. It
        depends only on hashes of what was submitted, so it is the same for unknown e-mails."""
        pair, address, _ = subjects
        window_start = now - PASSWORD_WINDOW
        waits = [
            password_wait(
                await self._failures(pair, window_start), PASSWORD_FAILURES_PER_EMAIL_AND_IP, now
            ),
            password_wait(
                await self._failures(address, window_start, reset_on_success=False),
                PASSWORD_FAILURES_PER_IP,
                now,
            ),
        ]
        return max((wait for wait in waits if wait is not None), default=None)

    async def _refuse(self, identity_id: uuid.UUID | None) -> None:
        """Refuse the password until a sign-in with an e-mail link. The statement runs for an
        unknown e-mail too (matching no row), so the timing stays the same."""
        result = await self.session.execute(
            update(UserIdentity)
            .where(
                UserIdentity.id == (identity_id or uuid.uuid4()),
                UserIdentity.requires_link_sign_in.is_(False),
            )
            .values(requires_link_sign_in=True)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            logger.warning("auth_password_refused_until_link_sign_in")

    # --- Setting, changing, removing ------------------------------------------------------------

    async def set_password(
        self,
        user: CurrentUserDTO,
        claims: TokenClaims | None,
        new_password: str,
        current_password: str | None,
        address: str,
    ) -> AuthMail | None:
        """Set or change the caller's password; the other sessions end. Returns the notice."""
        password = normalize_password(new_password)
        if weakness := password_weakness(password):
            raise password_too_weak(weakness)
        await self._reauthenticate(user, claims, current_password, address)
        await self.session.commit()  # no transaction stays open while the hash runs
        with self.hashing.admitted():
            encoded = await self.hashing.hash(password)
        now = _now()
        statement = pg_insert(UserIdentity).values(
            id=uuid.uuid4(),
            user_id=user.id,
            provider=PASSWORD_PROVIDER,
            subject_hash=self._hash(user_subject(user.id)),
            secret_data=encoded,
            created_at=now,
            requires_link_sign_in=False,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[UserIdentity.user_id],
                # A literal, not a bound parameter: Postgres matches it to the partial index.
                index_where=text(f"provider = '{PASSWORD_PROVIDER}'"),
                set_={
                    "subject_hash": statement.excluded.subject_hash,
                    "secret_data": statement.excluded.secret_data,
                    "requires_link_sign_in": False,
                },
            )
        )
        # Closed while the hash ran? Then this insert must not outlive the closing (all identities
        # of a closed account are deleted). The insert holds a key-share lock on the user row, so a
        # closing that commits later still deletes it.
        if not await self.properties.is_user_active(user.id):
            raise unknown_user()
        return await self._after_change(user, claims)

    async def remove_password(
        self,
        user: CurrentUserDTO,
        claims: TokenClaims | None,
        current_password: str | None,
        address: str,
    ) -> AuthMail | None:
        """Remove the caller's password; nothing to check or do when none is set."""
        if not await self.is_set(user.id):
            return None
        await self._reauthenticate(user, claims, current_password, address)
        await self.session.execute(
            delete(UserIdentity)
            .where(UserIdentity.user_id == user.id, UserIdentity.provider == PASSWORD_PROVIDER)
            .execution_options(synchronize_session=False)
        )
        return await self._after_change(user, claims)

    async def _reauthenticate(
        self,
        user: CurrentUserDTO,
        claims: TokenClaims | None,
        current_password: str | None,
        address: str,
    ) -> None:
        """A recent sign-in, or the right current password (counted like a login)."""
        if is_recent_sign_in(claims.auth_time if claims else None, _now()):
            return
        if current_password is None:
            raise reauthentication_required()
        contact = await self.properties.get_delivery_contact(user.id)
        # An account always has an e-mail; the id is a stand-in that still counts per account.
        email = contact.email if contact is not None else f"user-id:{user.id}"
        checked = await self._check(
            email, address, normalize_password(current_password), user_id=user.id
        )
        if checked != user.id:
            raise reauthentication_required()

    async def _after_change(
        self, user: CurrentUserDTO, claims: TokenClaims | None
    ) -> AuthMail | None:
        """End the other sessions and prepare the "your password was changed" e-mail."""
        await AuthService(self.session, self.settings).revoke_all(
            user.id, reason="password", keep=claims.session_id if claims else None
        )
        contact = await self.properties.get_delivery_contact(user.id)
        ref = await self.properties.user_ref(user.id)
        if contact is None or ref is None:
            return None
        return AuthMail(
            email=contact.email,
            locale=ref.locale or self.settings.DEFAULT_LOCALE,
            kind=PASSWORD_MAIL,
            link=self.settings.PUBLIC_BASE_URL,
        )
