"""Authentication (Bearer JWT, demo impersonation header) and building-role authorization."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import structlog
from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.exceptions import DomainError
from src.modules.property.schemas import BuildingRole, CurrentUserDTO
from src.modules.property.service import PropertyService, get_property_service

DEMO_USER_HEADER = "X-Demo-User-Id"

# auto_error=False: missing credentials are reported as our own JSON 401, not FastAPI's 403.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token (sub = user id).")
demo_user_header = APIKeyHeader(
    name=DEMO_USER_HEADER,
    auto_error=False,
    description="Demo mode only: act as any seeded user by id.",
)


def _unauthorized(error_code: str, message: str) -> DomainError:
    return DomainError(
        status_code=401,
        error_code=error_code,
        message=message,
        headers={"WWW-Authenticate": "Bearer"},  # RFC 6750
    )


@dataclass(frozen=True)
class TokenClaims:
    """What a verified access token says. `session_id` and `auth_time` are missing only in tokens
    minted without a session (tests, `AUTH_ALLOW_SESSIONLESS_TOKENS`)."""

    user_id: uuid.UUID
    session_id: uuid.UUID | None = None
    auth_time: datetime | None = None


def create_access_token(
    user_id: uuid.UUID,
    settings: Settings,
    expires_in: timedelta | None = None,
    *,
    session_id: uuid.UUID | None = None,
    auth_time: datetime | None = None,
) -> str:
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is not configured")
    now = datetime.now(UTC)
    ttl = (
        expires_in
        if expires_in is not None
        else timedelta(minutes=settings.JWT_ACCESS_TOKEN_TTL_MINUTES)
    )
    claims: dict[str, object] = {"sub": str(user_id), "iat": now, "exp": now + ttl, "typ": "access"}
    if session_id is not None:
        claims["sid"] = str(session_id)
    if auth_time is not None:
        claims["auth_time"] = int(auth_time.timestamp())
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    if not settings.JWT_SECRET_KEY:
        raise _unauthorized("INVALID_TOKEN", "Bearer authentication is not enabled.")
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
        if claims.get("typ") != "access":
            raise jwt.InvalidTokenError("not an access token")
        auth_time = claims.get("auth_time")
        return TokenClaims(
            user_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["sid"]) if "sid" in claims else None,
            auth_time=datetime.fromtimestamp(int(auth_time), UTC)
            if auth_time is not None
            else None,
        )
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise _unauthorized("INVALID_TOKEN", "The access token is invalid or expired.") from exc


async def _check_session(session: AsyncSession, claims: TokenClaims, settings: Settings) -> None:
    """A token is only as valid as its session (docs/ARCHITECTURE.md 4.4)."""
    if claims.session_id is None:
        if not settings.AUTH_ALLOW_SESSIONLESS_TOKENS:
            raise _unauthorized("INVALID_TOKEN", "The access token is invalid or expired.")
        return
    # Imported here: core never imports a module at import time.
    from src.modules.auth.service import session_is_active

    if not await session_is_active(session, claims.session_id, claims.user_id):
        raise _unauthorized("INVALID_TOKEN", "The access token is invalid or expired.")


async def _from_bearer(
    request: Request,
    bearer: HTTPAuthorizationCredentials,
    settings: Settings,
    property_service: PropertyService,
) -> uuid.UUID:
    claims = decode_access_token(bearer.credentials, settings)
    await _check_session(property_service.session, claims, settings)
    request.state.auth_claims = claims  # read by the auth router (sessions, recency)
    if claims.session_id is not None:
        trace.get_current_span().set_attribute("auth.session_id", str(claims.session_id))
    return claims.user_id


async def _load_user(property_service: PropertyService, user_id: uuid.UUID) -> CurrentUserDTO:
    current_user = await property_service.get_current_user(user_id)
    if current_user is None:
        raise _unauthorized("UNKNOWN_USER", "The user does not exist or is deactivated.")

    # Pseudonymous id and role only: never names, emails or phones (see AGENTS.md guardrails).
    trace.get_current_span().set_attributes(
        {"enduser.id": str(current_user.id), "enduser.role": current_user.building_role.value}
    )
    structlog.contextvars.bind_contextvars(user_id=str(current_user.id))
    return current_user


async def get_current_user(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    demo_user_id: str | None = Depends(demo_user_header),
    settings: Settings = Depends(get_settings),
    property_service: PropertyService = Depends(get_property_service),
) -> CurrentUserDTO:
    """Resolve the caller. A Bearer token wins over the demo header if both are present."""
    if bearer is not None:
        user_id = await _from_bearer(request, bearer, settings, property_service)
    elif demo_user_id is not None and settings.DEMO_MODE_ENABLED:
        try:
            user_id = uuid.UUID(demo_user_id)
        except ValueError as exc:
            raise _unauthorized("UNKNOWN_USER", f"{DEMO_USER_HEADER} is not a valid id.") from exc
    else:
        raise _unauthorized("NOT_AUTHENTICATED", "Authentication is required.")
    return await _load_user(property_service, user_id)


async def get_bearer_user(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    property_service: PropertyService = Depends(get_property_service),
) -> CurrentUserDTO:
    """Like get_current_user, but only a Bearer token from a sign-in; never the demo header (the
    account endpoints of docs/API.md section 7)."""
    if bearer is None:
        raise _unauthorized(
            "NOT_AUTHENTICATED", "Sign in first; the demo header does not reach this endpoint."
        )
    return await _load_user(
        property_service, await _from_bearer(request, bearer, settings, property_service)
    )


def require_building_roles(
    *roles: BuildingRole,
) -> Callable[..., Awaitable[CurrentUserDTO]]:
    """Dependency factory: `Depends(require_building_roles(BuildingRole.CHAIRMAN, ...))`."""
    allowed = frozenset(roles)

    async def dependency(
        current_user: CurrentUserDTO = Depends(get_current_user),
    ) -> CurrentUserDTO:
        if current_user.building_role not in allowed:
            raise DomainError(
                status_code=403,
                error_code="FORBIDDEN",
                message="Your building role does not allow this action.",
                extra={"required_roles": sorted(role.value for role in allowed)},
            )
        return current_user

    return dependency
