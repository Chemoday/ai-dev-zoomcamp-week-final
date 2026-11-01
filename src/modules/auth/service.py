"""Sessions: start, refresh with rotation, revoke, list (task E7/A2, docs/API.md section 7).

Refresh tokens are stored only as keyed hashes (AUTH_HASH_SECRET). A refresh locks the token row,
so two refreshes of one token are serialized. A token replaced less than REFRESH_GRACE ago still
gets an access token but no new cookie (the browser already holds the successor). A token
replaced longer ago counts as stolen and revokes the session.

No token, cookie or User-Agent is ever logged.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import Response
from opentelemetry import metrics
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.exceptions import DomainError
from src.core.security import create_access_token
from src.modules.auth.models import AuthSession, RefreshToken
from src.modules.auth.policies import (
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    RefreshDecision,
    device_kind,
    keyed_hash,
    new_refresh_token,
    refresh_decision,
    session_expiry,
)
from src.modules.auth.schemas import AuthMethod, IssuedSession, SessionResponse, SessionView

logger = structlog.get_logger(__name__)
_meter = metrics.get_meter(__name__)
_revoked_sessions = _meter.create_counter(
    "auth.revoked_sessions",
    description="Sessions ended: logout, logout_all, reuse (a stolen refresh token), password",
)


def _now() -> datetime:
    return datetime.now(UTC)


def session_expired() -> DomainError:
    return DomainError(
        status_code=401,
        error_code="SESSION_EXPIRED",
        message="The session has ended; sign in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def session_not_found() -> DomainError:
    return DomainError(status_code=404, error_code="SESSION_NOT_FOUND", message="No such session.")


async def session_is_active(
    session: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """For get_current_user: the session exists, belongs to the token's user, and has not ended."""
    row = await session.execute(
        select(AuthSession.revoked_at, AuthSession.expires_at).where(
            AuthSession.id == session_id, AuthSession.user_id == user_id
        )
    )
    found = row.first()
    return found is not None and found.revoked_at is None and found.expires_at > _now()


def set_refresh_cookie(response: Response, token: str, expires_at: datetime) -> None:
    """The cookie lives as long as the session would: the idle period, or less near the cap."""
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max(int((expires_at - _now()).total_seconds()), 0),
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _hash(self, token: str) -> str:
        return keyed_hash(self.settings.AUTH_HASH_SECRET.get_secret_value(), token)

    def _access(self, row: AuthSession) -> SessionResponse:
        ttl = timedelta(minutes=self.settings.JWT_ACCESS_TOKEN_TTL_MINUTES)
        token = create_access_token(
            row.user_id,
            self.settings,
            ttl,
            session_id=row.id,
            auth_time=row.authenticated_at,
        )
        return SessionResponse(
            access_token=token, expires_in=int(ttl.total_seconds()), session_id=row.id
        )

    async def _new_refresh_token(
        self, session_id: uuid.UUID, now: datetime
    ) -> tuple[str, uuid.UUID]:
        token = new_refresh_token()
        row = RefreshToken(session_id=session_id, token_hash=self._hash(token), created_at=now)
        self.session.add(row)
        await self.session.flush()
        return token, row.id

    # --- Starting -------------------------------------------------------------------------------

    async def start_session(
        self, user_id: uuid.UUID, method: AuthMethod, user_agent: str | None
    ) -> IssuedSession:
        """A new session after a successful sign-in (called by the sign-in flows of A3-A5)."""
        now = _now()
        cap = now + timedelta(days=self.settings.AUTH_SESSION_MAX_DAYS)
        row = AuthSession(
            user_id=user_id,
            auth_method=method,
            authenticated_at=now,
            last_used_at=now,
            expires_at=session_expiry(
                now, cap, timedelta(days=self.settings.AUTH_SESSION_IDLE_DAYS)
            ),
            max_expires_at=cap,
            device_kind=device_kind(user_agent),
        )
        self.session.add(row)
        await self.session.flush()
        token, _ = await self._new_refresh_token(row.id, now)
        return IssuedSession(
            response=self._access(row), refresh_token=token, expires_at=row.expires_at
        )

    # --- Refreshing -----------------------------------------------------------------------------

    async def refresh(self, token: str | None) -> IssuedSession:
        if not token:
            raise session_expired()
        now = _now()
        token_hash = self._hash(token)
        session_id = await self.session.scalar(
            select(RefreshToken.session_id).where(RefreshToken.token_hash == token_hash)
        )
        if session_id is None:
            raise session_expired()
        # Lock order everywhere: the session, then its tokens (also the order of the cascade
        # when the clean-up deletes a session), so a refresh never deadlocks with it.
        row = await self.session.scalar(
            select(AuthSession).where(AuthSession.id == session_id).with_for_update()
        )
        current = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        if row is None or current is None:  # deleted meanwhile by the clean-up
            raise session_expired()
        decision = refresh_decision(
            now=now,
            rotated_at=current.rotated_at,
            session_revoked_at=row.revoked_at,
            session_expires_at=row.expires_at,
        )
        if decision == RefreshDecision.EXPIRED:
            raise session_expired()
        if decision == RefreshDecision.REUSE:
            # A replaced token came back long after: someone copied it. End the session.
            await self._revoke(row, now, reason="reuse")
            # Committed here, so the revocation survives even a caller that lets the 401 below
            # roll back its transaction (the router itself turns it into a normal answer).
            await self.session.commit()
            logger.warning("auth_refresh_token_reused", session_id=str(row.id))
            raise session_expired()
        row.last_used_at = now
        row.expires_at = session_expiry(
            now, row.max_expires_at, timedelta(days=self.settings.AUTH_SESSION_IDLE_DAYS)
        )
        if decision == RefreshDecision.GRACE:
            await self.session.flush()
            return IssuedSession(
                response=self._access(row), refresh_token=None, expires_at=row.expires_at
            )
        current.rotated_at = now
        await self.session.flush()  # frees the "one current token" index for the successor
        successor, successor_id = await self._new_refresh_token(row.id, now)
        current.replaced_by_id = successor_id
        await self.session.flush()
        return IssuedSession(
            response=self._access(row), refresh_token=successor, expires_at=row.expires_at
        )

    # --- Ending ---------------------------------------------------------------------------------

    async def _revoke(self, row: AuthSession, now: datetime, *, reason: str) -> None:
        if row.revoked_at is None:
            row.revoked_at = now
            await self.session.flush()
            _revoked_sessions.add(1, {"reason": reason})

    async def logout(self, token: str | None) -> None:
        """End the session of this refresh cookie; silent when there is none (always 204)."""
        if not token:
            return
        current = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == self._hash(token))
        )
        if current is None:
            return
        row = await self.session.scalar(
            select(AuthSession).where(AuthSession.id == current.session_id).with_for_update()
        )
        if row is not None:
            await self._revoke(row, _now(), reason="logout")

    async def revoke_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        row = await self.session.scalar(
            select(AuthSession)
            .where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > _now(),
            )
            .with_for_update()
        )
        if row is None:  # someone else's, unknown or already ended: the same answer
            raise session_not_found()
        await self._revoke(row, _now(), reason="logout")

    async def revoke_all(self, user_id: uuid.UUID, *, reason: str = "logout_all") -> int:
        """End every session of the user ("sign out everywhere"; later a password change)."""
        result = await self.session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        count = result.rowcount or 0
        if count:
            _revoked_sessions.add(count, {"reason": reason})
        return count

    # --- Listing --------------------------------------------------------------------------------

    async def list_sessions(
        self, user_id: uuid.UUID, current_session_id: uuid.UUID | None
    ) -> list[SessionView]:
        rows = await self.session.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > _now(),
            )
            .order_by(AuthSession.authenticated_at.desc(), AuthSession.id)
        )
        return [
            SessionView(
                session_id=row.id,
                device_kind=row.device_kind,
                auth_method=row.auth_method,
                authenticated_at=row.authenticated_at,
                last_used_at=row.last_used_at,
                current=row.id == current_session_id,
            )
            for row in rows
        ]
